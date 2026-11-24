"""Summarize model impact and day-over-day changes from report counters."""

from html import escape

from run_report import FAILED, request_availability

METRICS = {
    "request_availability": ("请求可用率", None),
    "ttft_estimate": ("TTFT 达成率", "ttft_threshold_ms"),
    "tpot_estimate": ("TPOT 达成率", "tpot_threshold_ms_per_token"),
}


def insight_model_name(row):
    model = escape(row["model"])
    if row["sla"]["status"] == FAILED:
        return f'<strong class="insight-model-fail">{model}</strong>'
    return model


def http_impact(rows):
    failures = [row["request_availability"]["failures_5xx"] for row in rows]
    total = sum(failures)
    if not total:
        return (
            "当天无 5xx 日志条目。"
            if any(row["logs"].get("entries") for row in rows)
            else "当天无匹配网关日志。"
        )
    index = max(range(len(rows)), key=failures.__getitem__)
    row = rows[index]
    codes = {
        code: count
        for code, count in row["http_status"].items()
        if len(code) == 3 and code.startswith("5")
    }
    code = max(codes, key=codes.__getitem__)
    return (
        f"{insight_model_name(row)} 有 {failures[index]:,} 条 5xx，占全部模型 5xx "
        f"{failures[index] / total:.1%}。最常见的 5xx 状态码为 {escape(code)}（{codes[code]:,} 条）。"
    )


def latency_impact(rows, key):
    metrics = [(row, row[key]) for row in rows]
    amounts = [
        (
            row,
            values.get("denominator", 0) - values.get("within_threshold", 0),
            values.get("denominator", 0),
        )
        for row, values in metrics
    ]
    total = sum(missed for _, missed, _ in amounts)
    if not total:
        return "无超阈值样本" if any(samples for _, _, samples in amounts) else "无有效样本"
    row, missed, samples = max(amounts, key=lambda item: item[1])
    return f"{insight_model_name(row)} {missed:,} / {samples:,} 个超阈值，占全部超阈值样本 {missed / total:.1%}"


def daily_change(rows, previous):
    if previous is None:
        return "缺少前一日的日报数据。"
    by_model = {row["model"]: row for row in previous["rows"]}
    changes = []
    for row in rows:
        prior = by_model.get(row["model"])
        if not prior or prior["route"] != row["route"]:
            continue
        for key, (label, threshold) in METRICS.items():
            if threshold and prior["sla"][threshold] != row["sla"][threshold]:
                continue
            old_values = (
                request_availability(prior["http_status"])
                if threshold is None
                else prior[key]
            )
            new_values = row[key]
            old, new = old_values["ratio"], new_values["ratio"]
            if old is None or new is None:
                continue
            changes.append(
                (
                    new - old,
                    row,
                    label,
                    old,
                    new,
                    key,
                    old_values["denominator"],
                    new_values["denominator"],
                )
            )
    if not changes:
        return "与前一日无可比的指标样本。"
    change, row, label, old, new, key, old_count, new_count = max(
        changes, key=lambda item: abs(item[0])
    )
    unit = "条" if key == "request_availability" else "个"
    return (
        f'{insight_model_name(row)} 的{label}从 {escape(previous["business_date"])} 的 {old:.2%} 变为 {new:.2%}'
        f"（{change * 100:+.2f} 个百分点）。分母从 {old_count:,} {unit}变为 {new_count:,} {unit}。"
    )


def render_insights(data, previous):
    """Render three observations without modifying the SLA data."""
    rows = data["rows"]
    return f"""<section class="notes" aria-labelledby="report-insights">
  <h2 id="report-insights">数据洞察</h2>
  <ul>
    <li><strong>5xx 影响最大</strong>。{http_impact(rows)}</li>
    <li><strong>延迟超阈值贡献最大</strong>。TTFT 为 {latency_impact(rows, "ttft_estimate")}。TPOT 为 {latency_impact(rows, "tpot_estimate")}。</li>
    <li><strong>较前一日变化最大</strong>。{daily_change(rows, previous)}</li>
  </ul>
</section>"""
