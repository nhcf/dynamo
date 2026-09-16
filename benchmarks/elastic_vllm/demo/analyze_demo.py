#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# analyze_demo.py — Post-demo data analysis
# Splits Phase 1 data by topology period (2x2 vs 4x1) and compares metrics.
# Supports the report.md conclusion: TP=4 wins for input-heavy workloads.
#
# Usage:
#   python3 demo/analyze_demo.py [--output-dir demo_output]
#
# Output:
#   Writes analysis_results.json and prints a summary to stdout.

import argparse
import json
import os
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Post-demo data analysis")
    parser.add_argument("--output-dir", default="demo_output",
                        help="Demo output directory")
    return parser.parse_args()


def load_stats(output_dir: str) -> list[dict]:
    stats_file = os.path.join(output_dir, "load_stats.jsonl")
    records = []
    if not os.path.isfile(stats_file):
        return records
    with open(stats_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append(obj)
            except json.JSONDecodeError:
                continue
    return records


def load_monitor(output_dir: str, phase_idx: int) -> list[dict]:
    mon_file = os.path.join(output_dir, f"topo_monitor_{phase_idx}.jsonl")
    records = []
    if not os.path.isfile(mon_file):
        return records
    with open(mon_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append(obj)
            except json.JSONDecodeError:
                continue
    return records


def load_report(output_dir: str) -> dict:
    report_file = os.path.join(output_dir, "report.json")
    if not os.path.isfile(report_file):
        return {}
    with open(report_file, "r") as f:
        return json.load(f)


def find_topology_transitions(monitor_data: list[dict]) -> list[dict]:
    """Find moments where topology changed in monitor data."""
    transitions = []
    prev_topo = None
    for rec in monitor_data:
        topo = rec.get("topo", "?")
        if topo != prev_topo and prev_topo is not None:
            transitions.append({
                "elapsed": rec["elapsed"],
                "from": prev_topo,
                "to": topo,
            })
        prev_topo = topo
    return transitions


def split_stats_by_topology(stats: list[dict], monitor: list[dict], tag: str) -> dict[str, list[dict]]:
    """Split stats records by topology period based on monitor data."""
    if not monitor or not stats:
        return {}

    # Build topology timeline from monitor
    topo_periods = []
    for i, rec in enumerate(monitor):
        topo_periods.append({
            "elapsed": rec["elapsed"],
            "topo": rec.get("topo", "?"),
            "is_switching": rec.get("is_switching", "?"),
        })

    # Find transitions
    transitions = find_topology_transitions(monitor)

    # Build period boundaries
    periods = []
    if not transitions:
        # No transitions - single topology
        periods.append({
            "topo": topo_periods[0]["topo"] if topo_periods else "?",
            "start_t": 0,
            "end_t": float("inf"),
        })
    else:
        current_topo = topo_periods[0]["topo"] if topo_periods else "?"
        period_start = 0
        for tr in transitions:
            periods.append({
                "topo": current_topo,
                "start_t": period_start,
                "end_t": tr["elapsed"],
            })
            current_topo = tr["to"]
            period_start = tr["elapsed"]
        # Final period
        periods.append({
            "topo": current_topo,
            "start_t": period_start,
            "end_t": float("inf"),
        })

    # Assign stats to periods
    result = {}
    for period in periods:
        topo = period["topo"]
        # Exclude switching periods (where is_switching=true dominates)
        period_records = [s for s in stats
                          if s.get("tag") == tag
                          and not s.get("final", False)
                          and period["start_t"] <= s.get("t", 0) < period["end_t"]]
        if topo not in result:
            result[topo] = []
        result[topo].extend(period_records)

    return result


def compute_period_metrics(records: list[dict]) -> dict:
    """Compute aggregate metrics for a period."""
    if not records:
        return {"count": 0}

    thr_outs = [r["thr_out"] for r in records if r.get("thr_out", 0) > 0]
    ttft_p99s = [r["ttft_p99"] for r in records if r.get("ttft_p99", 0) > 0]
    tpot_means = [r["tpot_mean"] for r in records if r.get("tpot_mean", 0) > 0]
    ok_counts = [r.get("ok", 0) for r in records]
    err_counts = [r.get("err", 0) for r in records]

    return {
        "count": len(records),
        "total_ok": sum(ok_counts),
        "total_err": sum(err_counts),
        "thr_out": {
            "mean": sum(thr_outs) / len(thr_outs) if thr_outs else 0,
            "min": min(thr_outs) if thr_outs else 0,
            "max": max(thr_outs) if thr_outs else 0,
        },
        "ttft_p99": {
            "mean": sum(ttft_p99s) / len(ttft_p99s) if ttft_p99s else 0,
            "min": min(ttft_p99s) if ttft_p99s else 0,
            "max": max(ttft_p99s) if ttft_p99s else 0,
        },
        "tpot_mean": {
            "mean": sum(tpot_means) / len(tpot_means) if tpot_means else 0,
            "min": min(tpot_means) if tpot_means else 0,
            "max": max(tpot_means) if tpot_means else 0,
        },
    }


def compare_topologies(period_metrics: dict) -> dict:
    """Compare metrics across topologies for the same workload type."""
    comparisons = {}
    topologies = list(period_metrics.keys())
    if len(topologies) < 2:
        return comparisons

    # Compare first two topologies
    t1, t2 = topologies[0], topologies[1]
    m1, m2 = period_metrics[t1], period_metrics[t2]

    if m1["count"] == 0 or m2["count"] == 0:
        return comparisons

    for metric in ["thr_out", "ttft_p99", "tpot_mean"]:
        v1 = m1[metric]["mean"]
        v2 = m2[metric]["mean"]
        if v1 > 0:
            pct_change = ((v2 - v1) / v1) * 100
        else:
            pct_change = 0
        comparisons[metric] = {
            f"{t1}": round(v1, 1),
            f"{t2}": round(v2, 1),
            "pct_change": round(pct_change, 1),
            "winner": t2 if (metric == "thr_out" and pct_change > 0) or
                            (metric in ("ttft_p99", "tpot_mean") and pct_change < 0)
                     else t1,
        }

    return comparisons


def main():
    args = parse_args()
    output_dir = args.output_dir

    # Load data
    stats = load_stats(output_dir)
    report = load_report(output_dir)

    print("=" * 60)
    print("Elastic vLLM Demo — Post-Run Analysis")
    print("=" * 60)
    print(f"Stats records: {len(stats)}")
    print(f"Report: {json.dumps(report.get('expectations', []), indent=2)[:500]}")
    print()

    # Get unique tags
    tags = list(set(s.get("tag", "") for s in stats if s.get("tag")))
    print(f"Workload tags: {tags}")
    print()

    # Load Phase 1 monitor data (index 0)
    monitor_p1 = load_monitor(output_dir, 0)
    print(f"Phase 1 monitor records: {len(monitor_p1)}")

    transitions = find_topology_transitions(monitor_p1)
    print(f"Phase 1 topology transitions: {len(transitions)}")
    for tr in transitions:
        print(f"  @ {tr['elapsed']}s: {tr['from']} -> {tr['to']}")
    print()

    # Analyze Phase 1 (prefill_heavy) by topology
    results = {"phase1": {}, "phase2": {}}

    if monitor_p1 and "prefill_heavy" in tags:
        split = split_stats_by_topology(stats, monitor_p1, "prefill_heavy")
        print("Phase 1 (prefill_heavy) metrics by topology:")
        period_metrics = {}
        for topo, recs in split.items():
            m = compute_period_metrics(recs)
            period_metrics[topo] = m
            results["phase1"][topo] = m
            print(f"\n  {topo} ({m['count']} windows, {m['total_ok']} ok, {m['total_err']} err):")
            if m["count"] > 0:
                print(f"    Throughput:  {m['thr_out']['mean']:.1f} t/s (range: {m['thr_out']['min']:.1f}-{m['thr_out']['max']:.1f})")
                print(f"    TTFT p99:    {m['ttft_p99']['mean']:.0f} ms (range: {m['ttft_p99']['min']:.0f}-{m['ttft_p99']['max']:.0f})")
                print(f"    TPOT mean:   {m['tpot_mean']['mean']:.0f} ms (range: {m['tpot_mean']['min']:.0f}-{m['tpot_mean']['max']:.0f})")

        comparisons = compare_topologies(period_metrics)
        results["phase1_comparisons"] = comparisons
        if comparisons:
            print("\n  Comparison (2x2 vs 4x1 under prefill_heavy):")
            for metric, comp in comparisons.items():
                direction = "+" if comp["pct_change"] > 0 else ""
                print(f"    {metric}: {comp[list(comp.keys())[0]]} -> {comp[list(comp.keys())[1]]} ({direction}{comp['pct_change']}%) — {comp['winner']} wins")

    # Phase 2 (decode_heavy) — typically single topology
    if "decode_heavy" in tags:
        phase2_stats = [s for s in stats if s.get("tag") == "decode_heavy" and not s.get("final")]
        m = compute_period_metrics(phase2_stats)
        results["phase2"]["metrics"] = m
        print("\nPhase 2 (decode_heavy) metrics:")
        if m["count"] > 0:
            print(f"  {m['count']} windows, {m['total_ok']} ok, {m['total_err']} err")
            print(f"  Throughput:  {m['thr_out']['mean']:.1f} t/s")
            print(f"  TTFT p99:    {m['ttft_p99']['mean']:.0f} ms")
            print(f"  TPOT mean:   {m['tpot_mean']['mean']:.0f} ms")

    # Conclusion check
    print("\n" + "=" * 60)
    print("Report Conclusion Validation")
    print("=" * 60)

    phase1_comp = results.get("phase1_comparisons", {})
    if phase1_comp.get("thr_out", {}).get("winner") == "4x1":
        print("✓ CONFIRMED: TP=4 (4x1) wins for prefill-heavy throughput")
        if "thr_out" in phase1_comp:
            pct = phase1_comp["thr_out"]["pct_change"]
            print(f"  Throughput improvement: {pct:+.1f}%")
    elif phase1_comp:
        print("✗ NOT CONFIRMED: TP=4 (4x1) did NOT win for prefill-heavy throughput")
    else:
        print("? INSUFFICIENT DATA: No topology transition detected in Phase 1")

    # Check if report.md conclusion about decode_heavy can be validated
    phase2_metrics = results.get("phase2", {}).get("metrics", {})
    if phase2_metrics.get("count", 0) > 0:
        print(f"\nPhase 2 ran under single topology (2x2)")
        print(f"  To validate 2x2 advantage for decode_heavy, would need")
        print(f"  comparison data under 4x1 with same workload.")

    # Save results
    results_file = os.path.join(output_dir, "analysis_results.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nAnalysis results saved to: {results_file}")


if __name__ == "__main__":
    main()
