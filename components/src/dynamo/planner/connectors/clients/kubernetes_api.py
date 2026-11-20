# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import logging
from typing import Optional

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from dynamo.planner.errors import DynamoGraphDeploymentNotFoundError
from dynamo.planner.monitoring.dgd_services import (
    Service,
    get_component_type,
    get_components_by_name,
)
from dynamo.runtime.logging import configure_dynamo_logging

configure_dynamo_logging()
logger = logging.getLogger(__name__)

NVIDIA_API_GROUP = "nvidia.com"
DYNAMO_API_VERSION = "v1beta1"
DYNAMO_WORKER_METADATA_API_VERSION = "v1alpha1"
DGD_PLURAL = "dynamographdeployments"
DGDSA_PLURAL = "dynamographdeploymentscalingadapters"

# NanHu Infer owns the graph object on this platform. The planner keeps its
# Dynamo-shaped view of the graph internally (v1beta1 DGD: spec.components as a
# list, status.components keyed by name) and adapts the InferGraphDeployment
# into that shape at the API boundary, so nothing above this class changes.
NANHU_API_GROUP = "nanhu-tech.io"
NANHU_API_VERSION = "v1alpha1"
IGD_PLURAL = "infergraphdeployments"
IGDSA_PLURAL = "infergraphscalingadapters"

JSON_PATCH_CONTENT_TYPE = "application/json-patch+json"


def igd_as_dgd(igd: dict) -> dict:
    """Present an InferGraphDeployment the way the rest of the planner expects.

    spec.replicaSpecs is a map and status.components is a list of one-key maps;
    the Dynamo v1beta1 view wants a spec.components list and a status.components
    map. Components keep their original names because the scaling adapter is
    named after them (<igd>-<component>) and the planner writes into that name.

    The IGD declares a component flatly (image, commands, args, env on the
    ReplicaSpec) while the runtime reads them from podTemplate.spec.containers;
    the container is rebuilt here so model discovery and arg parsing keep
    working without teaching the rest of the planner about a second schema.
    """
    spec = dict(igd.get("spec") or {})
    components = []
    for name, replica_spec in (spec.get("replicaSpecs") or {}).items():
        if not replica_spec:
            continue
        component = dict(replica_spec)
        # The runtime calls the role "type"; the IGD calls it "replicaType".
        component["name"] = name
        component["type"] = component.pop("replicaType", "worker")
        component["replicas"] = component.get("replicas") or 1
        component["podTemplate"] = {
            "spec": {"containers": [_main_container_from_replica_spec(component)]}
        }
        components.append(component)

    status = dict(igd.get("status") or {})
    flat_status: dict = {}
    for group in status.get("components") or []:
        if isinstance(group, dict):
            flat_status.update(group)
    status["components"] = flat_status

    return {
        "metadata": igd.get("metadata") or {},
        "spec": {**spec, "components": components},
        "status": status,
    }


def _first(a: list) -> list:
    return a[0] if a else []


def _main_container_from_replica_spec(component: dict) -> dict:
    """Build the v1beta1 "main" container from a flat IGD ReplicaSpec."""
    return {
        "name": "main",
        "image": component.get("image", ""),
        "command": _first(component.get("commands") or []),
        "args": _first(component.get("args") or []),
        "env": component.get("env") or [],
        "ports": component.get("ports") or [],
        "resources": component.get("resources") or {},
    }


def get_current_k8s_namespace() -> str:
    """Get the current namespace if running inside a k8s cluster"""
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        # Fallback to 'default' if not running in k8s
        return "default"


class KubernetesAPI:
    def __init__(self, k8s_namespace: Optional[str] = None):
        # Load kubernetes configuration
        try:
            config.load_incluster_config()  # for in-cluster deployment
        except ConfigException:
            config.load_kube_config()  # for out-of-cluster deployment

        self.custom_api = client.CustomObjectsApi()
        self.current_namespace = k8s_namespace or get_current_k8s_namespace()

    def _get_graph_deployment_from_name(self, graph_deployment_name: str) -> dict:
        """Get the graph deployment from the InferGraphDeployment name.

        The IGD is adapted to the Dynamo v1beta1 shape so every caller above
        this method keeps working unchanged.
        """
        igd = self.custom_api.get_namespaced_custom_object(
            group=NANHU_API_GROUP,
            version=NANHU_API_VERSION,
            namespace=self.current_namespace,
            plural=IGD_PLURAL,
            name=graph_deployment_name,
        )
        return igd_as_dgd(igd)

    def list_graph_deployments(self) -> list[dict]:
        """List all InferGraphDeployments in the current namespace."""
        result = self.custom_api.list_namespaced_custom_object(
            group=NANHU_API_GROUP,
            version=NANHU_API_VERSION,
            namespace=self.current_namespace,
            plural=IGD_PLURAL,
        )
        return [igd_as_dgd(item) for item in result.get("items", [])]

    def get_graph_deployment(self, graph_deployment_name: str) -> dict:
        """
        Get the parent DynamoGraphDeployment

        Returns:
            The DynamoGraphDeployment object

        Raises:
            DynamoGraphDeploymentNotFoundError: If the parent graph deployment is not found
        """
        try:
            return self._get_graph_deployment_from_name(graph_deployment_name)
        except client.ApiException as e:
            if e.status == 404:
                raise DynamoGraphDeploymentNotFoundError(
                    deployment_name=graph_deployment_name,
                    namespace=self.current_namespace,
                )
            raise

    def update_service_replicas(
        self, graph_deployment_name: str, service_name: str, replicas: int
    ) -> None:
        """
        Update replicas for a component using the IGDSA Scale subresource when
        one exists. Falls back to writing replicaSpecs directly on the IGD when
        the component was not opted into adapter-managed scaling.

        Args:
            graph_deployment_name: Name of the InferGraphDeployment
            service_name: Name of the component in spec.replicaSpecs
            replicas: Desired number of replicas
        """
        # IGDSA naming convention: <igd-name>-<component-name>, built verbatim
        # by the operator. No case folding: component names are already
        # lowercase, and lowercasing would miss an adapter rather than find one.
        adapter_name = f"{graph_deployment_name}-{service_name}"

        try:
            # Try to scale via IGDSA Scale subresource
            self.custom_api.patch_namespaced_custom_object_scale(
                group=NANHU_API_GROUP,
                version=NANHU_API_VERSION,
                namespace=self.current_namespace,
                plural=IGDSA_PLURAL,
                name=adapter_name,
                body={"spec": {"replicas": replicas}},
            )
            logger.info(f"Scaled IGDSA {adapter_name} to {replicas} replicas")

        except client.ApiException as e:
            if e.status == 404:
                # No adapter for this component - write the field the operator
                # would otherwise take from the adapter.
                logger.info(
                    f"IGDSA {adapter_name} not found, falling back to IGD update"
                )
                self._patch_igd_replicas(graph_deployment_name, service_name, replicas)
            else:
                raise

    def _patch_igd_replicas(
        self, graph_deployment_name: str, service_name: str, replicas: int
    ) -> None:
        """Merge-patch one component's replicas on the IGD itself.

        A merge patch keeps every other field of that component intact, which
        the RFC 6902 patch used for the Dynamo object could not: the IGD stores
        components in a map, not in an indexed list.
        """
        self.custom_api.patch_namespaced_custom_object(
            group=NANHU_API_GROUP,
            version=NANHU_API_VERSION,
            namespace=self.current_namespace,
            plural=IGD_PLURAL,
            name=graph_deployment_name,
            body={"spec": {"replicaSpecs": {service_name: {"replicas": replicas}}}},
        )
        logger.info(
            f"Updated IGD {graph_deployment_name} component {service_name} "
            f"to {replicas} replicas"
        )

    def _update_dgd_replicas(
        self, graph_deployment_name: str, service_name: str, replicas: int
    ) -> None:
        """Update replicas directly in DGD when no DGDSA is available."""
        deployment = self.get_graph_deployment(graph_deployment_name)
        components = self._dgd_components(deployment, graph_deployment_name)
        self._patch_component_replicas(
            graph_deployment_name, components, service_name, replicas
        )
        logger.info(
            f"Updated DGD {graph_deployment_name} component {service_name} to {replicas} replicas"
        )

    @staticmethod
    def _dgd_components(deployment: dict, graph_deployment_name: str) -> list[dict]:
        components = deployment.get("spec", {}).get("components")
        if components is None:
            raise KeyError(
                f"DGD {graph_deployment_name!r} has no v1beta1 spec.components"
            )
        if not isinstance(components, list):
            raise TypeError(
                f"DGD {graph_deployment_name!r} spec.components must be a list"
            )
        return components

    def _patch_component_replicas(
        self,
        graph_deployment_name: str,
        components: list[dict],
        component_name: str,
        replicas: int,
    ) -> None:
        index = self._find_component_index(
            graph_deployment_name, components, component_name
        )
        patch = self._component_replicas_json_patch(index, component_name, replicas)
        self._patch_dgd_with_json_patch(graph_deployment_name, patch)

    @staticmethod
    def _find_component_index(
        graph_deployment_name: str, components: list[dict], component_name: str
    ) -> int:
        for index, component in enumerate(components):
            if component.get("name") == component_name:
                return index
        raise KeyError(
            f"component {component_name!r} not found in DGD {graph_deployment_name!r}"
        )

    @staticmethod
    def _component_replicas_json_patch(
        index: int, component_name: str, replicas: int
    ) -> list[dict]:
        return [
            {
                "op": "test",
                "path": f"/spec/components/{index}/name",
                "value": component_name,
            },
            {
                "op": "add",
                "path": f"/spec/components/{index}/replicas",
                "value": replicas,
            },
        ]

    def _patch_dgd_with_json_patch(
        self, graph_deployment_name: str, patch: list[dict]
    ) -> None:
        """Patch a v1beta1 DGD with RFC 6902 JSON Patch operations."""
        self.custom_api.api_client.call_api(
            "/apis/{group}/{version}/namespaces/{namespace}/{plural}/{name}",
            "PATCH",
            {
                "group": NVIDIA_API_GROUP,
                "version": DYNAMO_API_VERSION,
                "namespace": self.current_namespace,
                "plural": DGD_PLURAL,
                "name": graph_deployment_name,
            },
            [],
            {
                "Accept": "application/json",
                "Content-Type": JSON_PATCH_CONTENT_TYPE,
            },
            body=patch,
            response_type="object",
            auth_settings=["BearerToken"],
            _return_http_data_only=True,
            collection_formats={},
        )

    def update_graph_replicas(
        self, graph_deployment_name: str, component_name: str, replicas: int
    ) -> None:
        """
        Update replicas for a component. Now uses DGDSA when available.

        Deprecated: Use update_service_replicas() instead for clarity.
        This method is kept for backward compatibility.
        """
        self.update_service_replicas(graph_deployment_name, component_name, replicas)

    def is_deployment_ready(self, deployment: dict) -> bool:
        """Check if a graph deployment is ready"""

        conditions = deployment.get("status", {}).get("conditions", [])
        ready_condition = next(
            (c for c in conditions if c.get("type") == "Ready"), None
        )

        return ready_condition is not None and ready_condition.get("status") == "True"

    def get_service_replica_status(
        self, deployment: dict, service_name: str
    ) -> tuple[int, bool]:
        """
        Get the actual ready replica count for a component from DGD status.

        Returns:
            tuple[int, bool]: (replica_count, is_stable)
            - replica_count: number of replicas serving traffic (availableReplicas if present, else readyReplicas)
            - is_stable: no rollout is in progress (desired == updated == ready/available)
        """
        # Get desired replicas from spec
        service_spec = get_components_by_name(deployment).get(service_name, {})
        desired_replicas = Service(
            name=service_name, service=service_spec
        ).number_replicas()

        # Get status fields
        status = deployment.get("status", {})
        service_status = status.get("components", {}).get(service_name, {})
        available = service_status.get("availableReplicas")
        ready = service_status.get("readyReplicas", 0)
        updated = service_status.get("updatedReplicas", 0)

        # availableReplicas takes precedence over readyReplicas for the count
        # refer to ComponentReplicaStatus in deploy/operator/api/v1beta1/common.go
        if available is not None:
            traffic_serving_replicas = available
        else:
            traffic_serving_replicas = ready

        # Stable means: desired == updated == ready/available
        # This ensures we're not in a scale-up, scale-down, or rollout
        is_stable = desired_replicas == updated == traffic_serving_replicas

        return traffic_serving_replicas, is_stable

    async def wait_for_graph_deployment_ready(
        self,
        graph_deployment_name: str,
        include_planner: bool = True,
        max_attempts: int = 180,  # default: 30 minutes total
        delay_seconds: int = 10,  # default: check every 10 seconds
    ) -> None:
        """Wait for a graph deployment to be ready.

        Args:
            graph_deployment_name: Name of the DGD to wait for.
            include_planner: If False, skip components with type "planner"
                and check per-component readiness instead of the global DGD Ready
                condition. This avoids a circular wait when the planner itself
                is one of the services in the DGD.
            max_attempts: Maximum polling iterations.
            delay_seconds: Seconds between polls.
        """
        for attempt in range(max_attempts):
            await asyncio.sleep(delay_seconds)

            graph_deployment = self.get_graph_deployment(graph_deployment_name)

            if include_planner:
                conditions = graph_deployment.get("status", {}).get("conditions", [])
                ready_condition = next(
                    (c for c in conditions if c.get("type") == "Ready"), None
                )
                if ready_condition and ready_condition.get("status") == "True":
                    return

                logger.info(
                    f"[Attempt {attempt + 1}/{max_attempts}] "
                    f"(status: {ready_condition.get('status') if ready_condition else 'N/A'}, "
                    f"message: {ready_condition.get('message') if ready_condition else 'no condition found'})"
                )
            else:
                components = get_components_by_name(graph_deployment)
                not_ready: list[str] = []
                for component_name, component_spec in components.items():
                    if get_component_type(component_spec) == "planner":
                        continue
                    _, is_stable = self.get_service_replica_status(
                        graph_deployment, component_name
                    )
                    if not is_stable:
                        not_ready.append(component_name)

                if not not_ready:
                    return

                logger.info(
                    f"[Attempt {attempt + 1}/{max_attempts}] "
                    f"Waiting for components (excluding planner): "
                    f"not ready: {not_ready}"
                )

        raise TimeoutError(
            f"Graph deployment '{graph_deployment_name}' "
            f"is not ready after {max_attempts * delay_seconds} seconds"
        )
