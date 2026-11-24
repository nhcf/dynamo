#!/usr/bin/env python3
"""Read a saved Dynamo SLA report and rank its models by gateway 5xx count."""

import argparse
import json
import runpy
from datetime import date
from pathlib import Path


def summarize(report_path, business_date, report):
    parser = report["InlineReportParser"]()
    parser.feed(report_path.read_text(encoding="utf-8"))
    snapshot = json.loads("".join(parser.inline))
    if snapshot["business_date"] != business_date:
        raise ValueError("report business date does not match the requested date")

    rows = []
    for row in snapshot["rows"]:
        statuses = row["http_status"]
        entries = row["logs"]["entries"]
        if entries != sum(statuses.values()):
            raise ValueError(
                f"HTTP status counts do not match entries for {row['model']}"
            )
        failures = {
            code: count
            for code, count in sorted(statuses.items())
            if len(code) == 3 and code.startswith("5")
        }
        availability = report["request_availability"](statuses)
        failure_count = availability["failures_5xx"]
        rows.append(
            {
                "model": row["model"],
                "route": row["route"],
                "entries": entries,
                "http_5xx": failures,
                "failures_5xx": failure_count,
                "fraction_of_all_entries": failure_count / entries if entries else None,
                "availability_denominator": availability["denominator"],
                "request_availability": availability["ratio"],
            }
        )

    rows.sort(key=lambda row: (-row["failures_5xx"], row["model"]))
    return {
        "business_date": business_date,
        "start": snapshot["start"],
        "end": snapshot["end"],
        "total_5xx": sum(row["failures_5xx"] for row in rows),
        "models": rows,
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("date", type=date.fromisoformat, metavar="YYYY-MM-DD")
    args = cli.parse_args()
    report_dir = Path(__file__).resolve().parents[4] / "learning" / "daily-report"
    day = args.date.isoformat()
    try:
        report = runpy.run_path(str(report_dir / "run_report.py"))
        summary = summarize(report_dir / day / "index.html", day, report)
    except (OSError, ValueError, KeyError) as error:
        cli.error(str(error))
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
