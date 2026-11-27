#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Toggle the native LiteLLM OTel callback for A/B measurements.

Requires kubectl and PyYAML. Defaults to maas-prod/litellm-test.
Examples:
  python3 learning/full-otel-trace/maas-prod/litellm/litellm_otel.py status
  python3 learning/full-otel-trace/maas-prod/litellm/litellm_otel.py off --dry-run
  python3 learning/full-otel-trace/maas-prod/litellm/litellm_otel.py on

on/off restart the Deployment when configuration changes. Other callbacks,
environment variables and YAML settings are preserved semantically; changing
callbacks reserializes the ConfigMap's config.yaml (comments are not preserved).
This controls the native callback, not custom sitecustomize/HTTPX instrumentation.
"""

import argparse
import copy
import datetime
import hashlib
import json
import subprocess
import sys

import yaml

CALLBACK_FIELDS = (
    "callbacks",
    "success_callback",
    "failure_callback",
    "service_callbacks",
)
ENV_DEFAULTS = {
    "OTEL_EXPORTER": "otlp_grpc",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector.nhi.svc.cluster.local:4317",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
    "OTEL_SERVICE_NAME": "litellm-test",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_TRACES_SAMPLER": "parentbased_always_on",
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_PROPAGATORS": "tracecontext,baggage",
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "NO_CONTENT",
}
RUNTIME_CHECK = """
import json, os, urllib.request
request = urllib.request.Request(
    'http://127.0.0.1:4000/active/callbacks',
    headers={'Authorization': 'Bearer ' + os.environ['LITELLM_MASTER_KEY']})
with urllib.request.urlopen(request, timeout=15) as response:
    data = json.load(response)
callbacks = data.get('litellm.callbacks', [])
print(json.dumps({'otel_active': any(
    value == 'otel' or 'litellm.integrations.opentelemetry.OpenTelemetry' in str(value)
    for value in callbacks)}))
"""


def kubectl(args, command, payload=None):
    """Select the requested context before each request and also pin --context."""
    subprocess.run(
        ["kubectl", "config", "use-context", args.context],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = subprocess.run(
        ["kubectl", "--context", args.context, "-n", args.namespace, *command],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
        timeout=args.timeout + 30,
    )
    return result.stdout


def callbacks(settings, field):
    value = settings.get(field) or []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise ValueError(f"litellm_settings.{field} must be a list or string")
    return value


def callback_enabled(settings):
    return any("otel" in callbacks(settings, field) for field in CALLBACK_FIELDS)


def config_text(original, enabled):
    """Add one native callback or remove it from all configured callback lists."""
    config = yaml.safe_load(original)
    if not isinstance(config, dict):
        raise ValueError("config.yaml must be a YAML mapping")
    before = copy.deepcopy(config)
    settings = config.setdefault("litellm_settings", {})
    if enabled:
        if not callback_enabled(settings):
            settings["callbacks"] = [*callbacks(settings, "callbacks"), "otel"]
    else:
        for field in CALLBACK_FIELDS:
            values = callbacks(settings, field)
            if "otel" in values:
                settings[field] = [value for value in values if value != "otel"]
    if config == before:
        return original
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def environment(original, enabled, endpoint, service_name):
    desired = dict(ENV_DEFAULTS)
    desired["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    desired["OTEL_SERVICE_NAME"] = service_name
    if enabled:
        # Keep positions of existing entries so repeated `on` is idempotent.
        result = []
        for entry in original:
            name = entry["name"]
            if name in desired:
                result.append({"name": name, "value": desired.pop(name)})
            else:
                result.append(entry)
        result.extend({"name": name, "value": value} for name, value in desired.items())
        return result
    return [entry for entry in original if entry["name"] not in desired]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=["on", "off", "status"])
    parser.add_argument("--context", default="maas-prod")
    parser.add_argument("--namespace", default="litellm-test")
    parser.add_argument("--deployment", default="litellm-test")
    parser.add_argument("--configmap", default="litellm-test-config")
    parser.add_argument("--container", default="litellm")
    parser.add_argument(
        "--endpoint", default=ENV_DEFAULTS["OTEL_EXPORTER_OTLP_ENDPOINT"]
    )
    parser.add_argument("--service-name", default=ENV_DEFAULTS["OTEL_SERVICE_NAME"])
    parser.add_argument(
        "--timeout", type=int, default=180, help="rollout timeout in seconds"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show planned changes without patching or restarting",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    deploy = json.loads(
        kubectl(args, ["get", "deployment", args.deployment, "-o", "json"])
    )
    cm = json.loads(kubectl(args, ["get", "configmap", args.configmap, "-o", "json"]))
    pod = deploy["spec"]["template"]["spec"]
    containers = pod["containers"]
    index = next(
        (i for i, c in enumerate(containers) if c["name"] == args.container), None
    )
    if index is None:
        raise ValueError(f"Container {args.container} not found")
    volumes = {
        volume["name"]
        for volume in pod.get("volumes", [])
        if volume.get("configMap", {}).get("name") == args.configmap
    }
    if not any(
        mount["name"] in volumes for mount in containers[index].get("volumeMounts", [])
    ):
        raise ValueError("Selected ConfigMap is not mounted by the selected container")
    original = cm["data"]["config.yaml"]
    original_env = containers[index].get("env", [])
    print(
        f"Target: {args.context}/{args.namespace}/{args.deployment} ({args.container})"
    )
    if args.action == "status":
        settings = yaml.safe_load(original).get("litellm_settings", {})
        active = json.loads(
            kubectl(
                args,
                [
                    "exec",
                    "-i",
                    f"deploy/{args.deployment}",
                    "-c",
                    args.container,
                    "--",
                    "python",
                    "-S",
                    "-",
                ],
                RUNTIME_CHECK,
            )
        )
        print(
            json.dumps(
                {
                    "config_callback_enabled": callback_enabled(settings),
                    "runtime_otel_active": active["otel_active"],
                    "ready_replicas": deploy.get("status", {}).get("readyReplicas", 0),
                    "otel_environment": {
                        e["name"]: e.get("value", "<valueFrom>")
                        for e in original_env
                        if e["name"] in ENV_DEFAULTS
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    enabled = args.action == "on"
    updated = config_text(original, enabled)
    updated_env = environment(original_env, enabled, args.endpoint, args.service_name)
    cm_changed = updated != original
    env_changed = updated_env != original_env
    annotations = dict(
        deploy["spec"]["template"].get("metadata", {}).get("annotations") or {}
    )
    checksum = hashlib.sha256(updated.encode()).hexdigest()
    restart_needed = (
        cm_changed
        or env_changed
        or annotations.get("litellm-otel-config-sha256") != checksum
    )
    print(
        f"OTel: {args.action}; ConfigMap change: {cm_changed}; environment change: {env_changed}; rollout needed: {restart_needed}"
    )
    if args.dry_run:
        print("Dry run: no resources changed; no restart.")
        return
    if cm_changed:
        patch = [
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": cm["metadata"]["resourceVersion"],
            },
            {"op": "replace", "path": "/data/config.yaml", "value": updated},
        ]
        kubectl(
            args,
            [
                "patch",
                "configmap",
                args.configmap,
                "--type=json",
                "--patch-file=/dev/stdin",
            ],
            json.dumps(patch),
        )
    if restart_needed:
        # Persist the loaded-config checksum so retrying after a partial apply
        # still restarts pods when the ConfigMap patch previously succeeded.
        annotations["litellm-otel-config-sha256"] = checksum
        annotations["kubectl.kubernetes.io/restartedAt"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        patch = [
            {
                "op": "test",
                "path": "/metadata/resourceVersion",
                "value": deploy["metadata"]["resourceVersion"],
            },
            {
                "op": "add",
                "path": f"/spec/template/spec/containers/{index}/env",
                "value": updated_env,
            },
            {
                "op": "add",
                "path": "/spec/template/metadata/annotations",
                "value": annotations,
            },
        ]
        kubectl(
            args,
            [
                "patch",
                "deployment",
                args.deployment,
                "--type=json",
                "--patch-file=/dev/stdin",
            ],
            json.dumps(patch),
        )
    else:
        print("Configuration already matches; no restart needed.")
    print(
        kubectl(
            args,
            [
                "rollout",
                "status",
                f"deployment/{args.deployment}",
                f"--timeout={args.timeout}s",
            ],
        ).strip()
    )
    active = json.loads(
        kubectl(
            args,
            [
                "exec",
                "-i",
                f"deploy/{args.deployment}",
                "-c",
                args.container,
                "--",
                "python",
                "-S",
                "-",
            ],
            RUNTIME_CHECK,
        )
    )
    if active["otel_active"] != enabled:
        raise RuntimeError(
            "Runtime callback state differs from requested state; check LiteLLM logs and database callback overrides"
        )
    print(f"Verified runtime OTel {'enabled' if enabled else 'disabled'}.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(
            f"kubectl failed: {exc.stderr.strip()}\nIf an earlier patch succeeded, rerun the same command to finish applying the state.",
            file=sys.stderr,
        )
        sys.exit(1)
    except (
        ValueError,
        KeyError,
        RuntimeError,
        yaml.YAMLError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
