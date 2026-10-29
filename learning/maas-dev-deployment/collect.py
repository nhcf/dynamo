#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only maas-dev Dynamo audit. Does not query Kubernetes Secrets."""
import datetime
import json
import re
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
CTX = "maas-dev"


def cmd(args):
    r = subprocess.run(args, capture_output=True, text=True, timeout=50)
    if r.returncode:
        return {"error": r.stderr.strip(), "command": args}
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"text": r.stdout}


def k(*args):
    return cmd(
        ["kubectl", "--context=" + CTX, "--request-timeout=20s", *args, "-o", "json"]
    )


def safe(v):
    if isinstance(v, dict):
        return {
            k: (
                "[REDACTED]"
                if re.search(
                    r"password|token|credential|authorization|api.?key|client.?secret|private.?key|headers",
                    k,
                    re.I,
                )
                else safe(x)
            )
            for k, x in v.items()
            if k
            not in ("managedFields", "kubectl.kubernetes.io/last-applied-configuration")
        }
    if isinstance(v, list):
        return [safe(x) for x in v]
    if isinstance(v, str):
        v = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", v)
        v = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", v)
        v = re.sub(
            r"(?i)((?:password|token|api_key|secret_key)\s*[:=]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            v,
        )
    return v


def meta(o):
    return {
        k: o["metadata"].get(k)
        for k in ("namespace", "name", "labels", "creationTimestamp", "generation")
    }


def container(c):
    env = []
    for e in c.get("env", []):
        n = e["name"]
        entry = {"name": n}
        if "valueFrom" in e:
            entry["valueFrom"] = e["valueFrom"]
        elif re.search(r"TOKEN|PASSWORD|SECRET|AUTH|KEY|HEADERS", n, re.I):
            entry["value"] = "[REDACTED]"
        elif re.search(
            r"DYN|OTEL|NATS|ETCD|LOG|GROVE|NIXL|PORT|NAMESPACE|WATCH", n, re.I
        ):
            entry["value"] = safe(e.get("value"))
        else:
            entry["value"] = "[not retained]"
        env.append(entry)
    return {
        **{
            n: safe(c[n])
            for n in (
                "name",
                "image",
                "imagePullPolicy",
                "command",
                "args",
                "ports",
                "resources",
                "livenessProbe",
                "readinessProbe",
                "startupProbe",
                "volumeMounts",
            )
            if n in c
        },
        "env": env,
        "envFrom": c.get("envFrom", []),
    }


result = {
    "context": CTX,
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "collection_policy": "Read-only API and Helm metadata; no Secrets queried. Sensitive keys/values redacted.",
}
result["helm_releases"] = cmd(
    ["helm", "list", "--kube-context", CTX, "-A", "-o", "json"]
)
result["helm_user_values"] = safe(
    cmd(
        [
            "helm",
            "get",
            "values",
            "dynamo-system",
            "-n",
            "dynamo",
            "--kube-context",
            CTX,
            "-o",
            "json",
        ]
    )
)
result["helm_computed_values"] = safe(
    cmd(
        [
            "helm",
            "get",
            "values",
            "dynamo-system",
            "-n",
            "dynamo",
            "--kube-context",
            CTX,
            "--all",
            "-o",
            "json",
        ]
    )
)
for typ in [
    "dynamographdeployments",
    "dynamocomponentdeployments",
    "dynamographdeploymentrequests",
    "dynamomodels",
    "dynamographdeploymentscalingadapters",
]:
    obj = k("get", typ, "-A")
    result[typ] = {"count": len(obj["items"])} if "items" in obj else obj
pods = k("get", "pods", "-A")
result["dynamo_pods"] = []
result["related_pods"] = []
for p in pods.get("items", []):
    if p["metadata"]["namespace"] == "dynamo":
        result["dynamo_pods"].append(
            {
                **meta(p),
                "nodeName": p["spec"].get("nodeName"),
                "serviceAccountName": p["spec"].get("serviceAccountName"),
                "containers": [container(c) for c in p["spec"]["containers"]],
                "status": {
                    k: p["status"].get(k)
                    for k in ("phase", "conditions", "containerStatuses")
                },
                "volumes": p["spec"].get("volumes", []),
            }
        )
    elif any(
        re.search("prometheus|grafana|otel|tempo|loki|alloy|dcgm", c["image"], re.I)
        for c in p["spec"]["containers"]
    ):
        result["related_pods"].append(
            {
                **meta(p),
                "images": [c["image"] for c in p["spec"]["containers"]],
                "phase": p["status"].get("phase"),
            }
        )
for typ in [
    "deployments",
    "statefulsets",
    "services",
    "endpoints",
    "podmonitors",
    "servicemonitors",
]:
    obj = k("get", typ, "-n", "dynamo")
    if typ in ("deployments", "statefulsets"):
        result[typ] = [
            {
                **meta(o),
                "replicas": o["spec"].get("replicas"),
                "status": o.get("status"),
                "containers": [
                    container(c) for c in o["spec"]["template"]["spec"]["containers"]
                ],
            }
            for o in obj.get("items", [])
        ]
    else:
        result[typ] = safe(obj)
result["configmaps"] = []
for o in k("get", "configmaps", "-n", "dynamo").get("items", []):
    if o["metadata"]["name"].startswith(("dynamo-system", "grove-operator")):
        data = {}
        for name, value in o.get("data", {}).items():
            try:
                data[name] = safe(json.loads(value))
            except ValueError:
                # ConfigMap text may contain credentials; redact sensitive lines before saving.
                data[name] = "\n".join(
                    "[REDACTED CONFIG LINE]"
                    if re.search(
                        r"password|token|credential|authorization|secret|api.?key",
                        line,
                        re.I,
                    )
                    else line
                    for line in value.splitlines()
                )
        result["configmaps"].append({**meta(o), "data": data})
result["prometheus_resources"] = safe(
    k("get", "prometheuses.monitoring.coreos.com", "-A")
)
result["monitoring_services"] = []
for s in k("get", "services", "-A").get("items", []):
    if re.search("prometheus|otel|tempo|loki|dcgm", s["metadata"]["name"], re.I):
        result["monitoring_services"].append({**meta(s), "spec": s["spec"]})
(OUT / "deployment-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
)
print(
    json.dumps(
        {
            "captured_at": result["captured_at"],
            "dynamo_pods": [p["name"] for p in result["dynamo_pods"]],
            "resources": {
                t: result[t]
                for t in [
                    "dynamographdeployments",
                    "dynamocomponentdeployments",
                    "dynamographdeploymentrequests",
                    "dynamomodels",
                ]
            },
            "evidence": str(OUT / "deployment-evidence.json"),
        },
        ensure_ascii=False,
        indent=2,
    )
)
