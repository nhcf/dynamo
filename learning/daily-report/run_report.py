#!/usr/bin/env python3
"""Build the local model SLA report from read-only Higress Loki data."""

import argparse
import json
import math
import os
import shlex
import time
from collections import Counter
from datetime import date, datetime
from datetime import time as civil_time
from datetime import timedelta
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from string import Template
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
NANOSECONDS = 10**9
HOUR = 3600 * NANOSECONDS
PAGE_SIZE = 5000
REQUEST_AVAILABILITY_TARGET = 0.995
LATENCY_TARGET = 0.95
PASSED = "达标"
FAILED = "未达标"
UNDETERMINED = "暂无法判定"
GRAFANA_URL = "http://isg.fed.zhejianglab.org/grafana"
LOKI_UID = "fg3rvth6eufi8d"
ROUTE_MODELS = {
    "litellm-service-021sfm-236b-thinking": "021SFM-236B-Thinking",
    "litellm-service-021sfm-236b-instruct": "021SFM-236B-Instruct",
    "litellm-service-deepseek-v4-flash": "DeepSeek-V4-Flash-0731",
    "litellm-service-deepseek-v4-1-flash": "DeepSeek-V4.1-Flash-1017",
    "litellm-service-glm-5-3": "GLM-5.3",
    "litellm-service-kimi-k3": "Kimi-K3",
    "021-guarantee-litellm-service-021sfm-32b-thinking": "021SFM-32B-Thinking",
}
HTTP_SCOPE = r"^/maas/v1/(chat/completions|messages)([?].*)?$"
FIELDS = (
    "rid route_name response_code response_flags detail rt first total n "
    "reason done first_valid last_valid"
).split()


def build_query():
    routes = "|".join(ROUTE_MODELS)
    selector = (
        '{filename="/var/log/proxy/access.log",authority="i-nhi.zhejianglab.org",'
        f'method="POST",route_name=~"{routes}"}}'
    )
    outer = (
        ' | json p="path",rid="request_id",ai="ai_log",detail="response_code_details"'
    )
    outer += f' | p=~"{HTTP_SCOPE}"'
    inner = (
        ' | line_format "{{.ai}}"'
        ' | json rt="response_type",first="llm_first_token_duration",'
        'total="llm_service_duration",n="output_token",reason="finish_reason",'
        'done="stream_completed",first_valid="first_valid_output_ms",'
        'last_valid="last_valid_output_ms"'
    )
    format_string = " ".join("%q" for _ in FIELDS)
    attributes = " ".join("." + field for field in FIELDS)
    projection = f' | line_format `{{{{printf "{format_string}" {attributes}}}}}`'
    return selector + outer + inner + projection + " | keep filename"


def fetch_page(base_url, query, start, end):
    for attempt in range(3):
        try:
            params = urlencode(
                {
                    "start": start,
                    "end": end,
                    "query": query,
                    "limit": PAGE_SIZE,
                    "direction": "forward",
                }
            )
            url = f"{base_url}/loki/api/v1/query_range?{params}"
            with urlopen(url, timeout=65) as response:
                payload = json.load(response)
            if payload["status"] != "success" or payload.get("isPartial"):
                raise ValueError("Loki response was unsuccessful or partial")
            results = payload["data"]["result"]
            return sorted(
                (int(ts), line) for group in results for ts, line in group["values"]
            )
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"Loki HTTP {error.code}") from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))
    raise RuntimeError("page retry loop ended without a response")


def number(value):
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def blank():
    return {
        "logs": Counter(),
        "http_status": Counter(),
        "ttft_estimate": Counter(),
        "tpot_estimate": Counter(),
        "strict_timing_fields": Counter(),
    }


def is_reset(flags, detail):
    known = {"UF", "UR", "UC", "UT", "LR", "URX", "SI"}
    return bool(known.intersection(flags.split(","))) or detail.startswith(
        "upstream_reset"
    )


def aggregate(record, row, limit):
    (
        _,
        route,
        status,
        flags,
        detail,
        kind,
        first,
        total,
        tokens,
        reason,
        done,
        first_valid,
        last_valid,
    ) = record
    first, total, tokens = map(number, (first, total, tokens))
    strict_first, strict_last = map(number, (first_valid, last_valid))
    status_ok = status.isdigit() and len(status) == 3 and status.startswith("2")
    reset = is_reset(flags, detail)
    if kind == "normal":
        row["logs"]["non_stream_requests"] += 1
        return
    if kind != "stream":
        row["logs"]["unknown_response_type"] += 1
        return
    row["logs"]["stream_requests"] += 1
    row["strict_timing_fields"]["first_valid"] += strict_first is not None
    row["strict_timing_fields"]["last_valid"] += strict_last is not None
    row["strict_timing_fields"]["explicit_terminal_field"] += bool(reason or done)
    if first is None:
        row["ttft_estimate"]["unmeasurable"] += 1
    else:
        row["ttft_estimate"]["denominator"] += 1
        row["ttft_estimate"]["within_threshold"] += first <= 5000

    if not status_ok or reset or flags != "-":
        row["tpot_estimate"]["excluded_gateway_error_or_flag"] += 1
    elif tokens is not None and tokens.is_integer() and tokens < 2:
        row["tpot_estimate"]["excluded_under_two_tokens"] += 1
    elif (
        first is None
        or total is None
        or total < first
        or tokens is None
        or not tokens.is_integer()
    ):
        row["tpot_estimate"]["unmeasurable"] += 1
    else:
        tpot = (total - first) / (tokens - 1)
        row["tpot_estimate"]["denominator"] += 1
        row["tpot_estimate"]["within_threshold"] += tpot <= limit


def collect(day, base_url):
    """Collect a full Shanghai business day and return aggregate counters."""
    start = datetime.combine(day, civil_time(), SHANGHAI)
    end = datetime.combine(day + timedelta(days=1), civil_time(), SHANGHAI)
    start_ns = int(start.timestamp()) * NANOSECONDS
    end_ns = int(end.timestamp()) * NANOSECONDS
    query = build_query()
    rows = {route: blank() for route in ROUTE_MODELS}
    requests = {}
    conflict_routes = {}
    pages = 0
    for hour_start in range(start_ns, end_ns, HOUR):
        hour_end = hour_start + HOUR
        cursor = hour_start
        while cursor < hour_end:
            entries = fetch_page(base_url, query, cursor, hour_end)
            pages += 1
            boundary = entries[-1][0] if len(entries) == PAGE_SIZE else hour_end
            if boundary <= cursor:
                raise RuntimeError("Loki page is saturated at a single timestamp")
            for ts, line in entries:
                if ts >= boundary:
                    break
                record = tuple(shlex.split(line))
                if len(record) != len(FIELDS):
                    raise ValueError("unexpected Loki projection width")
                rid, route, status, *_ = record
                row = rows[route]
                row["logs"]["entries"] += 1
                row["http_status"][status] += 1
                if not rid:
                    row["logs"]["missing_request_id"] += 1
                    continue
                previous = requests.get(rid)
                if previous is not None:
                    row["logs"]["duplicate_request_id_entries"] += 1
                    if record[1:] != previous[1:]:
                        conflict_routes.setdefault(rid, set()).update(
                            (route, previous[1])
                        )
                requests[rid] = record
            cursor = boundary
        hour_number = (hour_start - start_ns) // HOUR + 1
        if hour_number % 6 == 0:
            print(f"{day}: hours={hour_number} pages={pages}", flush=True)

    for rid, record in requests.items():
        route = record[1]
        if rid in conflict_routes:
            for affected in conflict_routes[rid]:
                rows[affected]["logs"]["conflicting_request_id"] += 1
            continue
        row = rows[route]
        row["logs"]["unique_unambiguous_requests"] += 1
        limit = 200 if ROUTE_MODELS[route] == "Kimi-K3" else 50
        aggregate(record, row, limit)
    summary = {
        "business_date": day.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generated_at": datetime.now(SHANGHAI).isoformat(timespec="seconds"),
        "gateway_key_exclusion_applied": False,
        "loki_pages": pages,
        "rows": [],
    }
    for route, model in ROUTE_MODELS.items():
        row = rows[route]
        if row["logs"]["entries"] != sum(row["http_status"].values()):
            raise ValueError("HTTP status counts do not match raw entries")
        classified = sum(
            row["logs"][key]
            for key in (
                "stream_requests",
                "non_stream_requests",
                "unknown_response_type",
            )
        )
        if row["logs"]["unique_unambiguous_requests"] != classified:
            raise ValueError("response types do not match de-duplicated requests")
        summary["rows"].append({"model": model, "route": route, **row})
    return summary


def estimate(values):
    denominator = values.get("denominator", 0)
    numerator = values.get("within_threshold", 0)
    if not denominator:
        return "—<small>无可用观测</small>"
    ratio = numerator / denominator
    return f"<strong>{ratio:.2%}</strong><small>{numerator:,} / {denominator:,} 个请求</small>"


def count(values, name):
    return f"{values.get(name, 0):,}"


def metric_result(value, target):
    """Apply a target only to an eligible, unrounded observation."""
    if value is None:
        return {"status": UNDETERMINED, "reason": "无有效样本"}
    return {"status": PASSED if value >= target else FAILED, "reason": None}


def overall_result(metrics):
    """A confirmed failure takes precedence over missing metrics."""
    statuses = [metric["status"] for metric in metrics.values()]
    if FAILED in statuses:
        return FAILED
    if all(status == PASSED for status in statuses):
        return PASSED
    return UNDETERMINED


def status_badge(status):
    css = {PASSED: "status-pass", FAILED: "status-fail", UNDETERMINED: "status-pending"}
    return f'<span class="status {css[status]}">{status}</span>'


def metric_badge(result):
    reason = result["reason"]
    explanation = f"<small>{escape(reason)}</small>" if reason else ""
    return f'<div class="metric-status">{status_badge(result["status"])}{explanation}</div>'


def http_class_count(status_counts, prefix):
    return sum(
        count
        for code, count in status_counts.items()
        if len(code) == 3 and code.startswith(prefix)
    )


def request_availability(status_counts):
    failures = http_class_count(status_counts, "5")
    available = http_class_count(status_counts, "2") + http_class_count(
        status_counts, "3"
    )
    valid_requests = available + failures
    return {
        "numerator": available,
        "denominator": valid_requests,
        "failures_5xx": failures,
        "ratio": available / valid_requests if valid_requests else None,
    }


def http_rate_cell(count, entries):
    if not entries:
        return "—<small>无记录</small>"
    return f"<strong>{count / entries:.2%}</strong><small>{count:,} / {entries:,} 条</small>"


def rate_limit_rows(rows):
    affected = [
        (row["model"], row["http_status"]["429"], row["logs"]["entries"])
        for row in rows
        if row["http_status"].get("429")
    ]
    affected.sort(key=lambda item: (-item[1], item[0]))
    if not affected:
        return '<tr><td colspan="4">本期无匹配的 429 日志</td></tr>'
    return "".join(
        f'<tr><th scope="row">{escape(model)}</th><td>{count:,}</td>'
        f'<td>{entries:,}</td><td class="http-429"><strong>{count / entries:.2%}</strong></td></tr>'
        for model, count, entries in affected
    )


def render(data, insights=""):
    """Render and verify one standalone HTML report."""
    if data["gateway_key_exclusion_applied"]:
        raise ValueError(
            "Phase 1 requires a snapshot that includes matching zero2x requests"
        )
    data["rows"].sort(key=lambda row: (-row["logs"].get("entries", 0), row["model"]))
    rows = []
    audit = []
    for row in data["rows"]:
        model = escape(row["model"])
        logs, http = row["logs"], row["http_status"]
        entries = logs.get("entries", 0)
        http_ok = http_class_count(http, "2")
        http_ratio = http_ok / entries if entries else None
        availability = request_availability(http)
        availability_cell = http_rate_cell(
            availability["numerator"], availability["denominator"]
        )
        if availability["denominator"]:
            availability_cell += (
                f'<small>5xx {availability["failures_5xx"]:,} 条</small>'
            )
        limit = 200 if row["model"] == "Kimi-K3" else 50
        metrics = {
            "request_availability": metric_result(
                availability["ratio"], REQUEST_AVAILABILITY_TARGET
            )
        }
        latency_proportions = {}
        for metric in ("ttft", "tpot"):
            values = row[f"{metric}_estimate"]
            numerator, denominator = values.get("within_threshold", 0), values.get(
                "denominator", 0
            )
            if not 0 <= numerator <= denominator <= logs.get("stream_requests", 0):
                raise ValueError(f"invalid denominator for {model} {metric}")
            ratio = numerator / denominator if denominator else None
            values["ratio"] = ratio
            metrics[metric] = metric_result(ratio, LATENCY_TARGET)
            latency_proportions[metric] = ratio
        overall = overall_result(metrics)
        row["sla"] = {
            "request_availability_target": REQUEST_AVAILABILITY_TARGET,
            "ttft_target_proportion": LATENCY_TARGET,
            "ttft_threshold_ms": 5000,
            "tpot_target_proportion": LATENCY_TARGET,
            "tpot_threshold_ms_per_token": limit,
            "ttft_proportion": latency_proportions["ttft"],
            "tpot_proportion": latency_proportions["tpot"],
            "metrics": metrics,
            "status": overall,
        }
        row["http_2xx"] = {
            "numerator": http_ok,
            "denominator": entries,
            "ratio": http_ratio,
        }
        row["request_availability"] = availability
        rows.append(
            f"""<tr>
    <th scope="row">{model}</th>
    <td>{availability_cell}{metric_badge(metrics["request_availability"])}</td>
    <td>{estimate(row["ttft_estimate"])}<small>≤ 5 s</small>{metric_badge(metrics["ttft"])}</td>
    <td>{estimate(row["tpot_estimate"])}<small>≤ {limit} ms/token</small>{metric_badge(metrics["tpot"])}</td>
    <td>{status_badge(overall)}</td></tr>"""
        )
        strict = row["strict_timing_fields"]
        audit.append(
            f"""<tr><th scope="row">{model}</th>
    <td>{entries:,}</td><td>{count(logs, "unique_unambiguous_requests")}</td>
    <td>{count(logs, "stream_requests")}</td><td>{count(logs, "non_stream_requests")}</td>
    <td>{count(logs, "unknown_response_type")}</td>
    <td>{count(logs, "duplicate_request_id_entries")}</td>
    <td>{count(logs, "missing_request_id")} / {count(logs, "conflicting_request_id")}</td>
    <td>{count(strict, "first_valid")} / {count(strict, "last_valid")} / {count(strict, "explicit_terminal_field")}</td>
    <td>{count(row["ttft_estimate"], "unmeasurable")} / {count(row["tpot_estimate"], "unmeasurable")}</td>
    </tr>"""
        )

    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).replace(
        "<", "\\u003c"
    )
    template = Template(
        Path(__file__).with_name("report-template.html").read_text(encoding="utf-8")
    )
    start, end = (
        datetime.fromisoformat(data[key]).astimezone(SHANGHAI)
        for key in ("start", "end")
    )
    html = template.substitute(
        business_date=data["business_date"],
        window_start=start.strftime("%Y-%m-%d %H:%M"),
        window_end=end.strftime("%Y-%m-%d %H:%M"),
        request_availability_target=f"{REQUEST_AVAILABILITY_TARGET:.1%}",
        rows="".join(rows),
        rate_limits=rate_limit_rows(data["rows"]),
        audit="".join(audit),
        insights=insights,
        payload=payload,
    )
    parser = InlineReportParser()
    parser.feed(html)
    if (
        json.loads("".join(parser.inline)) != data
        or parser.external_assets
        or len(data["rows"]) != len(ROUTE_MODELS)
    ):
        raise ValueError("invalid standalone report")
    return html


class InlineReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reading = False
        self.inline = []
        self.external_assets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "report-data":
            self.reading = True
        if set(attrs).intersection({"src", "href"}):
            self.external_assets.append(attrs)

    def handle_endtag(self, tag):
        if tag == "script":
            self.reading = False

    def handle_data(self, content):
        if self.reading:
            self.inline.append(content)


def main():
    """Collect the previous Shanghai business day unless a date is supplied."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", type=date.fromisoformat, help="business date as YYYY-MM-DD"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent
    )
    args = parser.parse_args()
    today = datetime.now(SHANGHAI).date()
    day = args.date or today - timedelta(days=1)
    if day >= today:
        parser.error("select a completed business day")

    grafana_url = os.environ.get("GRAFANA_URL", GRAFANA_URL).rstrip("/")
    base_url = f"{grafana_url}/api/datasources/proxy/uid/{LOKI_UID}"
    data = collect(day, base_url)
    html = render(data)
    output_dir = args.output_dir / day.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "index.html"
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output_dir, delete=False
    ) as file:
        temp = Path(file.name)
        try:
            file.write(html)
            file.flush()
            temp.replace(output)
        finally:
            temp.unlink(missing_ok=True)
    print(f"{day}: wrote {output} using {data['loki_pages']} Loki pages", flush=True)


if __name__ == "__main__":
    main()
