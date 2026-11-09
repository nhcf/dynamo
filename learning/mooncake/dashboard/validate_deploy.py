#!/usr/bin/env python3
"""Validate live PromQL; --deploy also saves this dedicated dashboard.

Credentials: GRAFANA_USER and GRAFANA_PASSWORD (basic auth), or GRAFANA_TOKEN
(bearer). No credentials are written to the evidence or dashboard files.
"""
import argparse
import base64
import concurrent.futures
import datetime
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = os.environ.get("GRAFANA_URL", "http://isg.fed.zhejianglab.org/grafana").rstrip(
    "/"
)
DS = "34afaf30-b0a7-4abb-939a-5df0ef66b219"


def require(condition, message):
    """Keep validation active even when Python optimization is enabled."""
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args()
    if os.environ.get("GRAFANA_TOKEN"):
        auth = "Bearer " + os.environ["GRAFANA_TOKEN"]
    else:
        pair = (
            os.environ.get("GRAFANA_USER", "admin")
            + ":"
            + os.environ["GRAFANA_PASSWORD"]
        )
        auth = "Basic " + base64.b64encode(pair.encode()).decode()

    request_lock = threading.Lock()
    last_request = [0.0]

    def request(path, payload=None):
        req = urllib.request.Request(
            BASE + path,
            data=None
            if payload is None
            else json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Authorization": auth, "Content-Type": "application/json"},
        )
        for attempt in range(5):
            # The shared Grafana proxy can rate-limit rapid validation requests.
            with request_lock:
                time.sleep(max(0, 0.25 - (time.monotonic() - last_request[0])))
                last_request[0] = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code != 429 or payload is not None or attempt == 4:
                    raise
                time.sleep(
                    min(
                        10,
                        max(2**attempt, float(error.headers.get("Retry-After", "0"))),
                    )
                )

    def query(expr, range_query=False):
        params = {"query": expr, "time": now}
        method = "query"
        if range_query:
            method = "query_range"
            params = {"query": expr, "start": now - 3600, "end": now, "step": 60}
        result = request(
            "/api/datasources/proxy/uid/"
            + DS
            + "/api/v1/"
            + method
            + "?"
            + urllib.parse.urlencode(params)
        )
        require(
            result.get("status") == "success" and not result.get("isPartial", False),
            result.get("error", "Partial response"),
        )
        return result["data"]["result"]

    now = int(time.time())
    candidate = json.loads((ROOT / "mooncake-master-dashboard.json").read_text())
    uid = candidate["uid"]
    before = None
    try:
        before = request("/api/dashboards/uid/" + uid)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    selector = 'job="igd_mooncake_scrape",cluster="v5000_prod_ack01",namespace="nanhu"'
    headless_recent = query(
        "count_over_time(up{" + selector + ',service=~".*-headless"}[2m])'
    )
    require(not headless_recent, "Headless still has samples in the last 2 minutes")
    active = query("count_over_time(up{" + selector + "}[2m])")
    require(
        active
        and all(
            not r["metric"].get("service", "").endswith("-headless") for r in active
        ),
        "No fresh ordinary Service scrape targets",
    )
    targets = query(
        "count by (cluster,namespace,pod,endpoint) (count_over_time(up{"
        + selector
        + "}[2m]))"
    )
    require(
        all(float(r["value"][1]) == 1 for r in targets),
        "Expected one active scrape series per endpoint",
    )
    ordinary = selector + ',service!~".*-headless"'
    health = query("up{" + ordinary + "}")
    sample_age = query("time() - timestamp(up{" + ordinary + "})")
    require(
        len(health) == len(targets) and all(float(r["value"][1]) == 1 for r in health),
        "Ordinary Service scrape unhealthy",
    )
    require(
        len(sample_age) == len(targets)
        and all(0 <= float(r["value"][1]) < 90 for r in sample_age),
        "Ordinary Service samples stale",
    )
    inventory = query("count by (__name__,pod) ({" + ordinary + "})")
    pods = sorted({row["metric"]["pod"] for row in targets})
    require(pods, "No Mooncake scrape targets found")
    checks = []
    items = [
        (p, t, pod, window, False)
        for p in candidate["panels"]
        for t in p.get("targets", [])
        for pod, window in [(".*", "5m"), (".*", "1h")]
        + [(re.escape(pod), "5m") for pod in pods]
    ]
    items += [
        (p, t, ".*", "5m", True)
        for p in candidate["panels"]
        if p["type"] == "timeseries"
        for t in p.get("targets", [])
    ]

    def check(item):
        p, t, pattern, window, is_range = item
        expr = (
            t["expr"]
            .replace("$cluster", "v5000_prod_ack01")
            .replace("$namespace", "nanhu")
            .replace("${pod:regex}", pattern.replace("\\", "\\\\"))
            .replace("$window", window)
        )
        rows = query(expr, is_range)
        identities = []
        for row in rows:
            labels = row["metric"]
            require(
                labels.get("cluster") == "v5000_prod_ack01"
                and labels.get("namespace") == "nanhu",
                (p["id"], labels),
            )
            require(re.fullmatch(pattern, labels["pod"]), (p["id"], labels))
            require(
                "service" not in labels, ("Duplicate Service dimension leaked", p["id"])
            )
            identity = tuple(sorted(labels.items()))
            require(identity not in identities, ("Duplicate output identity", p["id"]))
            identities.append(identity)
            values = row.get("values", [row["value"]] if "value" in row else [])
            for _, value in values:
                number = float(value)
                require(math.isfinite(number) and number >= 0, (p["id"], number))
                if p["fieldConfig"]["defaults"]["unit"] == "percentunit":
                    require(number <= 1.000001, (p["id"], "Ratio above 1", number))
        return {
            "panel_id": p["id"],
            "ref_id": t["refId"],
            "pod": pattern,
            "window": window,
            "mode": "range" if is_range else "instant",
            "series": len(rows),
            "status": "pass",
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        checks = list(pool.map(check, items))
    print(
        f"PASS: {len(checks)} queries, {len(pods)} Pods; isolation, deduplication and numeric checks",
        flush=True,
    )
    evidence = {
        "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "uid": uid,
        "dashboard_sha256": hashlib.sha256(
            (ROOT / "mooncake-master-dashboard.json").read_bytes()
        ).hexdigest(),
        "panel_count": sum(
            p["type"] not in ["row", "text"] for p in candidate["panels"]
        ),
        "query_count": sum(len(p.get("targets", [])) for p in candidate["panels"]),
        "validation_summary": {
            "queries_passed": len(checks),
            "instant_checks": sum(c["mode"] == "instant" for c in checks),
            "range_checks": sum(c["mode"] == "range" for c in checks),
            "pod_isolation": True,
            "duplicate_service_labels_removed": True,
            "finite_nonnegative_values": True,
            "ratios_within_unit_interval": True,
        },
        "targets": [
            {**r["metric"], "active_scrape_series": int(float(r["value"][1]))}
            for r in targets
        ],
        "metric_names_per_pod": {
            pod: len(
                {
                    r["metric"]["__name__"]
                    for r in inventory
                    if r["metric"]["pod"] == pod
                }
            )
            for pod in pods
        },
        "scrape_transition": {
            "headless_has_no_samples_2m": not headless_recent,
            "ordinary_services_up": all(float(r["value"][1]) == 1 for r in health),
            "max_sample_age_seconds": max(float(r["value"][1]) for r in sample_age),
        },
    }
    if args.deploy:
        payload = {
            "dashboard": candidate,
            "overwrite": False,
            "message": "fix(grafana): use ordinary Service after stopping headless scrapes",
        }
        if before:
            fresh = request("/api/dashboards/uid/" + uid)
            require(
                fresh["dashboard"] == before["dashboard"],
                "Concurrent dashboard edit; rerun",
            )
            backup_dir = Path.home() / ".local/state/dynamo/grafana-backups"
            backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%SZ"
            )
            backup = (
                backup_dir / f"{uid}-v{before['dashboard']['version']}-{stamp}.json"
            )
            backup.write_text(json.dumps(before, ensure_ascii=False, indent=2) + "\n")
            backup.chmod(0o600)
            evidence["backup_path"] = str(backup)
            payload["dashboard"]["id"] = before["dashboard"]["id"]
            payload["dashboard"]["version"] = before["dashboard"]["version"]
            payload["folderUid"] = before["meta"].get("folderUid", "")
        saved = request("/api/dashboards/db", payload)
        require(saved["status"] == "success", saved)
        after = request("/api/dashboards/uid/" + uid)
        for key in ["panels", "templating", "title", "uid", "refresh", "time"]:
            require(after["dashboard"][key] == candidate[key], key)
        evidence["deployment"] = {
            "status": saved["status"],
            "version": after["dashboard"]["version"],
            "url": BASE + saved["url"].removeprefix("/grafana"),
            "readback_match": True,
        }
        print(json.dumps(evidence["deployment"], ensure_ascii=False), flush=True)
    filename = "deployment-evidence.json" if args.deploy else "validation-evidence.json"
    (ROOT / filename).write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
