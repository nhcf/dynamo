#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# demo_tui.py — Elastic TP/PP Switch Monitor TUI (Textual + Rich)
# Pure observer — never triggers a switch call
#
# Features:
#   - Shared-X dual scatter subplots: TTFT p99 + Throughput
#   - Log Y scale to spread normal differences across chart height
#   - Phase coloring (cyan/green/magenta/... per conc phase)
#   - Phase boundaries marked with │ yellow vertical lines
#   - Normal points • solid, final summary rows ○ hollow dim
#   - Load Info panel showing current phase parameters
#   - Topology status + Metrics snapshot
#   - Event log with phase/expectation color-coding
#   - All shell events displayed via TUI (no stdout interference)
#
# Usage:
#   python3 demo/demo_tui.py [options]
#
# Options:
#   --fe-url          frontend URL (default http://localhost:9090)
#   --ctrl-url        control-plane URL (default http://localhost:9091)
#   --log-file        frontend.log path (default logs/frontend.log)
#   --stats-file      load-stats JSONL path (default demo_output/load_stats.jsonl)
#   --events-file     events-log path (default demo_output/events.log)
#   --load-info       current-load JSON path (default demo_output/current_load.json)
#   --interval        polling interval in seconds (default 2)
#   --history         chart history length in data points (default 120)
#   --up-threshold    UP threshold line (default 10)
#   --down-threshold  DOWN threshold line (default 2)

import argparse
import json
import math
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static
from textual.worker import Worker, get_current_worker


# ===================== CLI Parsing =====================

def parse_args():
    parser = argparse.ArgumentParser(
        description="TUI monitor for Elastic TP/PP Switch"
    )
    parser.add_argument("--fe-url", default="http://localhost:9090",
                        help="Frontend URL (default: http://localhost:9090)")
    parser.add_argument("--ctrl-url", default="http://localhost:9091",
                        help="Control plane URL (default: http://localhost:9091)")
    parser.add_argument("--log-file", default="logs/frontend.log",
                        help="Frontend log file path")
    parser.add_argument("--stats-file", default="demo_output/load_stats.jsonl",
                        help="Load stats JSONL file path")
    parser.add_argument("--events-file", default="demo_output/events.log",
                        help="Events log file path")
    parser.add_argument("--load-info", default="demo_output/current_load.json",
                        help="Current load info JSON file path")
    parser.add_argument("--interval", type=int, default=2,
                        help="Polling interval in seconds (default: 2)")
    parser.add_argument("--history", type=int, default=120,
                        help="Chart history length in data points (default: 120)")
    parser.add_argument("--up-threshold", type=int, default=0,
                        help="UP threshold line (0 = auto from thresholds.json)")
    parser.add_argument("--down-threshold", type=int, default=0,
                        help="DOWN threshold line (0 = auto from thresholds.json)")
    parser.add_argument("--thresholds-file", default="demo_output/thresholds.json",
                        help="Thresholds JSON file for auto-detection")
    return parser.parse_args()


# ===================== Data Point =====================

@dataclass
class DataPoint:
    """Single measurement from load_stats JSONL."""
    t: float
    conc: int
    thr_out: float
    ttft_p99: float
    tpot_mean: float
    is_final: bool = False
    phase_idx: int = 0


# ===================== State =====================

@dataclass
class State:
    # Topology
    tp: int = 2
    pp: int = 2
    is_switching: bool = False
    failed: bool = False
    campaigns: int = 0
    strategy_label: str = "[2x2]"

    # Metrics snapshot
    active_requests: float = 0
    prev_active: float = 0
    thr_out: float = 0
    prev_thr_out: float = 0
    ttft_p99: float = 0
    prev_ttft_p99: float = 0
    tpot_mean: float = 0
    prev_tpot_mean: float = 0
    ok_pct: int = 100
    prev_ok_pct: int = 100

    # Scatter chart data points
    data_points: list = field(default_factory=list)
    prev_conc: int = -1
    phase_idx: int = 0

    # Switch events: list of (tick, direction)
    switch_events: list = field(default_factory=list)

    # Event log (max 200 entries)
    event_log: list = field(default_factory=list)

    # Load info from current_load.json
    load_info: dict = field(default_factory=dict)

    # Tick
    tick: int = 0
    start_time: float = field(default_factory=time.time)

    # File read positions
    stats_pos: int = 0
    events_pos: int = 0
    log_pos: int = 0

    # Dynamic thresholds (loaded from thresholds.json at runtime)
    up_threshold: int = 0
    down_threshold: int = 0
    thresholds_loaded: bool = False


# ===================== Data Collection =====================

def http_get(url: str, timeout: int = 5) -> Optional[str]:
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def http_post_json(url: str, data: dict, timeout: int = 5) -> Optional[str]:
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body,
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def fetch_topology(state: State, ctrl_url: str):
    resp = http_post_json(f"{ctrl_url}/engine/control/parallel_strategy_state", {})
    if resp is None:
        return
    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        return

    new_tp = data.get("tensor_parallel_size", state.tp)
    new_pp = data.get("pipeline_parallel_size", state.pp)
    new_switching = data.get("is_switching", False)
    new_failed = data.get("failed", False)

    # Detect switch events
    if new_switching and not state.is_switching:
        if new_tp > state.tp or new_pp < state.pp:
            direction = "UP"
        else:
            direction = "DOWN"
        state.switch_events.append((state.tick, direction))
        add_event(state, f"[SWITCH] {direction} -> {new_tp}x{new_pp}")

    state.tp = new_tp
    state.pp = new_pp
    state.is_switching = new_switching
    state.failed = new_failed
    state.strategy_label = f"[{state.tp}x{state.pp}]"


def fetch_metrics(state: State, fe_url: str):
    resp = http_get(f"{fe_url}/metrics")
    if resp is None:
        return
    for line in resp.splitlines():
        if line.startswith("dynamo_frontend_active_requests"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    state.prev_active = state.active_requests
                    state.active_requests = float(parts[1])
                except ValueError:
                    pass
            break


def read_stats_file(state: State, stats_file: str):
    if not os.path.isfile(stats_file):
        return
    try:
        file_size = os.path.getsize(stats_file)
    except OSError:
        return
    if file_size <= state.stats_pos:
        state.stats_pos = file_size
        return
    try:
        with open(stats_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(state.stats_pos)
            new_data = f.read()
            state.stats_pos = f.tell()
    except OSError:
        return

    for line in new_data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        is_final = obj.get("final", False)
        conc = obj.get("conc", 0)
        t_val = obj.get("t", 0)

        # Detect phase transition (conc change)
        if state.prev_conc >= 0 and conc != state.prev_conc:
            state.phase_idx += 1
        state.prev_conc = conc

        # Create data point for scatter charts
        dp = DataPoint(
            t=t_val,
            conc=conc,
            thr_out=obj.get("thr_out", 0),
            ttft_p99=obj.get("ttft_p99", 0),
            tpot_mean=obj.get("tpot_mean", 0),
            is_final=is_final,
            phase_idx=state.phase_idx,
        )
        state.data_points.append(dp)

        # Cap data points to prevent unbounded memory growth
        if len(state.data_points) > 10000:
            state.data_points = state.data_points[-8000:]

        # Update scalar metrics (for MetricsPanel) — skip final summary rows
        if not is_final:
            state.prev_thr_out = state.thr_out
            state.thr_out = dp.thr_out
            state.prev_ttft_p99 = state.ttft_p99
            state.ttft_p99 = dp.ttft_p99
            state.prev_tpot_mean = state.tpot_mean
            state.tpot_mean = dp.tpot_mean
            ok = obj.get("ok", 0)
            err = obj.get("err", 0)
            total = ok + err
            state.prev_ok_pct = state.ok_pct
            state.ok_pct = (ok * 100 // total) if total > 0 else 100


def read_events_file(state: State, events_file: str):
    if not os.path.isfile(events_file):
        return
    try:
        file_size = os.path.getsize(events_file)
    except OSError:
        return
    if file_size <= state.events_pos:
        state.events_pos = file_size
        return
    try:
        with open(events_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(state.events_pos)
            new_data = f.read()
            state.events_pos = f.tell()
    except OSError:
        return

    for line in new_data.splitlines():
        line = line.strip()
        if line:
            add_event(state, line)


def read_log_events(state: State, log_file: str):
    if not os.path.isfile(log_file):
        return
    try:
        file_size = os.path.getsize(log_file)
    except OSError:
        return
    if file_size <= state.log_pos:
        state.log_pos = file_size
        return
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(state.log_pos)
            new_data = f.read()
            state.log_pos = f.tell()
    except OSError:
        return

    for line in new_data.splitlines():
        # Track campaign count but do NOT add to event log
        if re.search(r"campaign.*finished", line, re.IGNORECASE):
            state.campaigns += 1


def read_load_info(state: State, load_info_file: str):
    if not os.path.isfile(load_info_file):
        return
    try:
        with open(load_info_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        state.load_info = data
    except (json.JSONDecodeError, OSError):
        pass


def read_thresholds_file(state: State, thresholds_file: str, args):
    """Load threshold values from thresholds.json written by demo_run.sh.
    Only loads once; CLI --up/down-threshold takes precedence if non-zero."""
    if state.thresholds_loaded:
        return
    # CLI overrides take precedence
    if args.up_threshold > 0 and args.down_threshold > 0:
        state.up_threshold = args.up_threshold
        state.down_threshold = args.down_threshold
        state.thresholds_loaded = True
        return
    # Try loading from file
    if not os.path.isfile(thresholds_file):
        return
    try:
        with open(thresholds_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        up = data.get("up_threshold", 0)
        down = data.get("down_threshold", 0)
        if up > 0:
            state.up_threshold = up
        if down > 0:
            state.down_threshold = down
        if state.up_threshold > 0 or state.down_threshold > 0:
            state.thresholds_loaded = True
            add_event(state, f"[RUN] Thresholds loaded: UP≥{state.up_threshold} DOWN≤{state.down_threshold}")
    except (json.JSONDecodeError, OSError):
        pass


def add_event(state: State, msg: str):
    elapsed = format_elapsed(state)
    state.event_log.append(f"{elapsed} {msg}")
    if len(state.event_log) > 200:
        state.event_log = state.event_log[-200:]


def check_shutdown(args) -> bool:
    """Check if demo_run.sh has written a shutdown signal file."""
    shutdown_file = os.path.join(os.path.dirname(args.events_file), ".tui_shutdown")
    return os.path.isfile(shutdown_file)


def poll_all(state: State, args):
    # Check for external shutdown signal
    if check_shutdown(args):
        return "shutdown"
    read_thresholds_file(state, args.thresholds_file, args)
    fetch_topology(state, args.ctrl_url)
    fetch_metrics(state, args.fe_url)
    read_stats_file(state, args.stats_file)
    read_events_file(state, args.events_file)
    read_log_events(state, args.log_file)
    read_load_info(state, args.load_info)
    state.tick += 1
    return None


# ===================== Formatting Helpers =====================

def format_elapsed(state: State) -> str:
    diff = int(time.time() - state.start_time)
    return f"{diff // 60:02d}:{diff % 60:02d}"


def format_delta(curr: float, prev: float, unit: str = "", inverse: bool = False) -> str:
    delta = int(round(curr)) - int(round(prev))
    if inverse:
        delta = -delta
    if delta > 0:
        return f"\u25b2 +{delta}{unit}"
    elif delta < 0:
        return f"\u25bc {delta}{unit}"
    else:
        return f"  0{unit}"


# ===================== Scatter Chart Rendering =====================

PHASE_COLORS = ["cyan", "green", "magenta", "yellow", "red", "blue"]


def _fmt_axis(v: float) -> str:
    """Format axis value compactly."""
    if v >= 10000:
        return f"{v / 1000:.0f}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return f"{v:.0f}"


def render_scatter_chart(
    points: list,
    y_field: str,
    width: int,
    height: int,
    *,
    title: str = "",
    y_unit: str = "",
    log_scale: bool = True,
) -> Text:
    """Render a scatter subplot with phase coloring and log Y scale.

    - Each phase (different conc) gets a distinct color from PHASE_COLORS
    - Phase boundaries are marked with │ yellow vertical lines
    - Normal data points use • (solid), final summary rows use ○ (hollow dim)
    - Log Y scale spreads normal differences across the chart height,
      preventing outlier spikes from compressing the visible range
    """
    if width < 20 or height < 5:
        return Text("  (terminal too small)", style="dim")

    chart_h = height - 2  # title + x-axis
    y_w = 7
    cw = max(width - y_w - 1, 8)

    if not points:
        return Text("  (waiting for data...)", style="dim")

    # X range — use sequential index (not t field, which may be inaccurate)
    n_pts = len(points)
    x_min = 0
    x_max = max(n_pts - 1, 1)

    # Y range — exclude final rows for scale calculation
    vals = [getattr(p, y_field) for p in points if not p.is_final]
    if not vals:
        vals = [getattr(p, y_field) for p in points]
    positive_vals = [abs(v) for v in vals if v > 0]
    if not positive_vals:
        positive_vals = [1]

    # Percentile-based Y range — focus on the bulk of data so that
    # normal variation (e.g. 800–1000 ms) fills most of the chart height.
    # Outliers beyond the percentile window are clipped to the edges.
    sorted_vals = sorted(positive_vals)
    p5_idx = max(0, int(len(sorted_vals) * 0.05) - 1)
    p95_idx = min(len(sorted_vals) - 1, max(0, int(len(sorted_vals) * 0.95) - 1))

    if log_scale:
        y_lo = sorted_vals[p5_idx]
        y_hi = sorted_vals[p95_idx]
        # Guarantee at least a small visible range
        if y_hi <= y_lo:
            y_hi = y_lo * 2 if y_lo > 0 else 1
        # Add 10 % padding on each side so data doesn't sit on the very edge
        log_lo = math.log10(y_lo)
        log_hi = math.log10(y_hi)
        pad = (log_hi - log_lo) * 0.1
        y_lo = 10 ** (log_lo - pad) if y_lo > 0 else 10 ** (log_lo - pad)
        y_hi = 10 ** (log_hi + pad)
        scale_hint = f"(log {y_lo:.0f}~{y_hi:.0f}{y_unit})"
    else:
        y_lo = 0
        y_hi = max(sorted_vals[p95_idx], 1)
        scale_hint = f"(y_max={_fmt_axis(y_hi)}{y_unit})"

    # Value <-> fraction mapping
    def val_to_frac(v: float) -> float:
        if log_scale:
            if v <= 0:
                return 0.0
            log_v = math.log10(v)
            log_lo_v = math.log10(y_lo) if y_lo > 0 else 0.0
            log_hi_v = math.log10(y_hi) if y_hi > 1 else 1.0
            span = log_hi_v - log_lo_v
            if span <= 0:
                return 0.5
            return max(0.0, min(1.0, (log_v - log_lo_v) / span))
        else:
            if y_hi <= 0:
                return 0.0
            return max(0.0, min(1.0, v / y_hi))

    def frac_to_val(frac: float) -> float:
        if log_scale:
            log_lo_v = math.log10(y_lo) if y_lo > 0 else 0.0
            log_hi_v = math.log10(y_hi) if y_hi > 1 else 1.0
            span = log_hi_v - log_lo_v
            return 10 ** (log_lo_v + frac * span)
        else:
            return frac * y_hi

    # Map each point to a grid cell; prefer non-final at same cell
    grid: dict = {}
    for i, p in enumerate(points):
        col = int((i - x_min) / (x_max - x_min) * (cw - 1))
        col = max(0, min(cw - 1, col))
        v = getattr(p, y_field)
        frac = val_to_frac(v)
        row = int((1 - frac) * (chart_h - 1))
        row = max(0, min(chart_h - 1, row))
        key = (col, row)
        if key not in grid or (not p.is_final and grid[key].is_final):
            grid[key] = p

    # Phase boundary columns
    phase_start_cols: dict = {}
    for i, p in enumerate(points):
        col = int((i - x_min) / (x_max - x_min) * (cw - 1))
        col = max(0, min(cw - 1, col))
        if p.phase_idx not in phase_start_cols:
            phase_start_cols[p.phase_idx] = col

    # Build canvas row by row
    result = Text()

    # Title line
    result.append(f"  {title}", style="bold white")
    result.append(f"  {scale_hint}", style="dim")
    result.append("\n")

    for r in range(chart_h):
        # Y-axis labels
        if r == 0:
            label_val = frac_to_val(1.0)
            result.append(f"{_fmt_axis(label_val):>6}\u2507", style="dim")
        elif r == chart_h - 1:
            if log_scale:
                result.append(f"{_fmt_axis(y_lo):>6}\u2507", style="dim")
            else:
                result.append("     0\u2507", style="dim")
        elif chart_h > 4 and r == chart_h // 2:
            row_frac = 1.0 - r / (chart_h - 1)
            label_val = frac_to_val(row_frac)
            result.append(f"{_fmt_axis(label_val):>6}\u2507", style="dim")
        else:
            result.append("      \u2507", style="dim")

        # Chart content
        for c in range(cw):
            key = (c, r)
            if key in grid:
                p = grid[key]
                color = PHASE_COLORS[p.phase_idx % len(PHASE_COLORS)]
                if p.is_final:
                    result.append("\u25CB", style=f"dim {color}")  # ○ hollow
                else:
                    result.append("\u2022", style=color)  # • solid
            else:
                # Phase boundary │
                is_boundary = False
                for pidx, pcol in phase_start_cols.items():
                    if pidx > 0 and c == pcol:
                        is_boundary = True
                        break
                if is_boundary:
                    result.append("\u2502", style="bold yellow")
                else:
                    result.append(" ")

        if r < chart_h - 1:
            result.append("\n")

    # X axis with sample-index labels
    result.append("\n")
    result.append("      \u2514", style="dim")
    n_ticks = min(5, cw // 8)
    if n_ticks > 0:
        x_line = [" "] * cw
        for i in range(n_ticks + 1):
            frac = i / n_ticks
            col = int(frac * (cw - 1))
            idx_val = x_min + frac * (x_max - x_min)
            label = f"#{int(idx_val)}"
            for j, ch in enumerate(label):
                pos = col + j
                if 0 <= pos < cw:
                    x_line[pos] = ch
        result.append("".join(x_line), style="dim")

    return result


# ===================== Textual Widgets =====================

class TopologyPanel(Static):
    """Compact topology + load info panel."""

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state
        info = state.load_info

        t = Text()
        t.append("Topology & Load\n", style="bold cyan")

        # Topology
        t.append("  ")
        if state.is_switching:
            t.append(f"TP={state.tp} PP={state.pp}", style="bold yellow blink")
            t.append(" \u21bb", style="bold yellow")
        elif state.failed:
            t.append(f"TP={state.tp} PP={state.pp}", style="bold red")
            t.append(" FAIL", style="bold red")
        else:
            t.append(f"TP={state.tp} PP={state.pp}", style="bold green")
            t.append(f" {state.strategy_label}", style="green")
        t.append("\n")

        # Load info from JSON
        if info:
            phase = info.get("phase_name", "")
            status = info.get("status", "")
            t.append(f"  {phase}\n", style="white")
            t.append("  Status: ", style="dim")
            if status == "loading":
                t.append("LOADING", style="bold green")
            elif status == "waiting":
                t.append("WAITING", style="bold yellow")
            elif status == "done":
                t.append("DONE", style="dim")
            else:
                t.append(status, style="white")
            t.append("\n")

            inp = info.get("input_len", 0)
            out = info.get("output_len", 0)
            conc = info.get("conc", 0)
            tag = info.get("tag", "")
            dur = info.get("duration", 0)
            if inp or out or conc:
                t.append(f"  in={inp} out={out} C={conc}", style="cyan")
                if dur:
                    t.append(f" dur={dur}s", style="dim")
                t.append("\n")
            if tag:
                t.append(f"  tag={tag}\n", style="dim")
        else:
            t.append("  (no active phase)\n", style="dim")

        return t


class MetricsPanel(Static):
    """Metrics snapshot panel with delta indicators."""

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state

        t = Text()
        t.append("Metrics Snapshot\n", style="bold cyan")

        # Header
        t.append(f"  {'Active':<10} {'Thr(t/s)':<12} {'TTFT(p99)':<14} {'TPOT(mean)':<12} {'OK%':<5}\n", style="bold white")

        # Values
        t.append(f"  {int(state.active_requests):<10} ")
        t.append(f"{state.thr_out:<12.1f} ")
        t.append(f"{state.ttft_p99:<14.0f} ")
        t.append(f"{state.tpot_mean:<12.0f} ")
        t.append(f"{state.ok_pct}%")

        return t


class ScatterChartWidget(Static):
    """Scatter chart widget with phase coloring and log Y scale."""

    def __init__(self, title: str, y_field: str, y_unit: str = "",
                 log_scale: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.chart_title = title
        self.y_field = y_field
        self.y_unit = y_unit
        self.log_scale = log_scale

    def render(self) -> Text:
        state = self.app.monitor_state
        return render_scatter_chart(
            state.data_points,
            self.y_field,
            self.size.width,
            self.size.height,
            title=self.chart_title,
            y_unit=self.y_unit,
            log_scale=self.log_scale,
        )


class EventLogPanel(Static):
    """Event log with color-coded entries."""

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state

        t = Text()
        t.append("Event Log\n", style="bold cyan")

        # Show last N events based on widget height
        max_lines = max(self.size.height - 3, 4)
        events = state.event_log[-max_lines:]

        for ev in events:
            if "[SWITCH]" in ev:
                t.append(f"  {ev}\n", style="bold yellow")
            elif "[RUN]" in ev:
                t.append(f"  {ev}\n", style="bold white")
            elif "[LOAD]" in ev:
                t.append(f"  {ev}\n", style="cyan")
            elif "[EXPECT]" in ev:
                if "PASS" in ev:
                    t.append(f"  {ev}\n", style="bold green")
                elif "FAIL" in ev:
                    t.append(f"  {ev}\n", style="bold red")
                else:
                    t.append(f"  {ev}\n", style="white")
            else:
                t.append(f"  {ev}\n", style="dim white")

        return t


class StatusBar(Static):
    """Bottom status bar."""

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state
        elapsed = format_elapsed(state)

        t = Text()
        t.append(f"  Tick:{state.tick:<4d}  Elapsed:{elapsed}  ", style="bold cyan")
        t.append(f"Topo:{state.tp}x{state.pp}", style="green")
        if state.is_switching:
            t.append(" \u21bbSWITCHING", style="bold yellow")
        t.append("  ", style="cyan")
        t.append("q", style="bold white")
        t.append("=quit", style="cyan")
        return t


# ===================== CSS =====================

CSS = """
Screen {
    layout: vertical;
    background: $surface;
}

#top-row {
    layout: horizontal;
    height: 8;
    margin: 0 1;
}

#topo-panel {
    width: 1fr;
    height: 100%;
    border: round darkcyan;
    padding: 0 1;
    margin-right: 1;
}

#metrics-panel {
    width: 2fr;
    height: 100%;
    border: round darkcyan;
    padding: 0 1;
}

#charts {
    layout: vertical;
    height: 1fr;
    margin: 0 1;
}

.chart-box {
    height: 1fr;
    min-height: 5;
    border: round darkcyan;
    padding: 0 1;
}

#event-log {
    height: 8;
    border: round darkcyan;
    padding: 0 1;
    margin: 0 1;
}

#status-bar {
    dock: bottom;
    height: 1;
    background: $primary-darken-2;
    color: $text;
    padding: 0 1;
}
"""


# ===================== Main App =====================

class ElasticMonitorApp(App):
    """Elastic TP/PP Switch Monitor — Textual TUI."""

    TITLE = "Elastic TP/PP Switch Monitor"

    CSS = CSS

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, args, **kwargs):
        super().__init__(**kwargs)
        self.monitor_args = args
        self.monitor_state = State()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="top-row"):
            yield TopologyPanel(id="topo-panel")
            yield MetricsPanel(id="metrics-panel")

        with Vertical(id="charts"):
            yield ScatterChartWidget(
                "TTFT p99 (ms)",
                "ttft_p99",
                y_unit="ms",
                log_scale=True,
                classes="chart-box",
                id="chart-ttft",
            )
            yield ScatterChartWidget(
                "Throughput (out tok/s)",
                "thr_out",
                y_unit=" tok/s",
                log_scale=True,
                classes="chart-box",
                id="chart-thr",
            )

        yield EventLogPanel(id="event-log")
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        poll_all(self.monitor_state, self.monitor_args)
        self.set_interval(self.monitor_args.interval, self._poll_and_refresh)

    def _poll_and_refresh(self) -> None:
        result = poll_all(self.monitor_state, self.monitor_args)
        if result == "shutdown":
            self.exit()
            return
        for widget in self.query(Static):
            widget.refresh()


# ===================== Entry Point =====================

def run():
    args = parse_args()
    app = ElasticMonitorApp(args)
    app.run()


if __name__ == "__main__":
    run()
