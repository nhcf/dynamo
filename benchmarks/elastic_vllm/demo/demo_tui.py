#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# demo_tui.py — Elastic TP/PP Switch Monitor TUI (Textual + Rich)
# Pure observer — never triggers a switch call
#
# Features:
#   - Area-style line charts for Concurrency, Throughput, TTFT, TPOT
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
import os
import re
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, Grid
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

    # History (4 metric curves)
    hist_active: deque = field(default_factory=deque)
    hist_thr: deque = field(default_factory=deque)
    hist_ttft: deque = field(default_factory=deque)
    hist_tpot: deque = field(default_factory=deque)

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
        if obj.get("final", False):
            continue

        state.prev_thr_out = state.thr_out
        state.thr_out = obj.get("thr_out", 0)
        state.prev_ttft_p99 = state.ttft_p99
        state.ttft_p99 = obj.get("ttft_p99", 0)
        state.prev_tpot_mean = state.tpot_mean
        state.tpot_mean = obj.get("tpot_mean", 0)
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


def update_history(state: State, history: int):
    state.hist_active.append(state.active_requests)
    state.hist_thr.append(state.thr_out)
    state.hist_ttft.append(state.ttft_p99)
    state.hist_tpot.append(state.tpot_mean)
    for dq in (state.hist_active, state.hist_thr, state.hist_ttft, state.hist_tpot):
        while len(dq) > history:
            dq.popleft()


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
    update_history(state, args.history)
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


# ===================== Area Chart Rendering =====================

def render_area_chart(
    data: deque,
    width: int,
    height: int,
    *,
    y_max: float = 0,
    color: str = "green",
    thresholds: list = None,
    switch_events: list = None,
    tick: int = 0,
    history: int = 120,
) -> Text:
    """Render an area-style line chart using Unicode line-drawing characters.

    - Thin ─╱╲ line at the curve edge with slope connectors
    - Dim ░ fill below the curve
    - Dashed ┄ threshold lines
    - Dashed ┆ switch-event markers
    """
    n = len(data)
    if n < 2 or width < 14 or height < 3:
        return Text("  (waiting for data...)", style="dim")

    y_w = 6  # width for y-axis labels (e.g. "  40┤")
    cw = max(width - y_w - 1, 8)

    # Downsample to chart width
    data_list = list(data)
    if n <= cw:
        samples = [float(v) for v in data_list]
    else:
        bucket = n / cw
        samples = []
        for i in range(cw):
            s = int(i * bucket)
            e = min(int((i + 1) * bucket), n)
            chunk = data_list[s:e]
            if chunk:
                samples.append(sum(float(v) for v in chunk) / len(chunk))
            else:
                samples.append(0.0)

    if not samples or len(samples) < 2:
        return Text("  (waiting for data...)", style="dim")

    # Y range — use P95 to avoid outlier spikes compressing the chart
    sorted_samples = sorted(abs(v) for v in samples)
    p95_idx = max(0, int(len(sorted_samples) * 0.95) - 1)
    data_p95 = sorted_samples[p95_idx]
    ym = max(y_max, data_p95, 1)

    # Compute curve rows (0 = top, height-1 = bottom)
    curve_rows = []
    for v in samples:
        frac = min(v / ym, 1.0) if ym > 0 else 0
        row = int((1 - frac) * (height - 1))
        curve_rows.append(max(0, min(height - 1, row)))

    # Precompute curve line characters based on slope
    line_chars = []
    for c in range(len(curve_rows)):
        if c > 0:
            prev_cr = curve_rows[c - 1]
            cr = curve_rows[c]
            if cr < prev_cr:      # curve goes UP visually
                line_chars.append("\u2571")   # ╱
            elif cr > prev_cr:    # curve goes DOWN visually
                line_chars.append("\u2572")   # ╲
            else:
                line_chars.append("\u2500")   # ─
        else:
            line_chars.append("\u2500")       # ─

    # Precompute threshold rows
    thr_rows = {}
    if thresholds:
        for tv, tl, ts in thresholds:
            tr = int((1 - min(tv / ym, 1.0)) * (height - 1)) if ym > 0 else height - 1
            tr = max(0, min(height - 1, tr))
            thr_rows[tr] = (tl, ts)

    # Build output row by row
    result = Text()
    for r in range(height):
        # Y-axis label
        if r == 0:
            result.append(f"{_fmt_axis(ym):>5}\u2507", style="dim")
        elif r == height - 1:
            result.append("    0\u2507", style="dim")
        elif height > 4 and r == height // 2:
            result.append(f"{_fmt_axis(ym / 2):>5}\u2507", style="dim")
        else:
            result.append("     \u2507", style="dim")

        # Chart content
        for c in range(len(curve_rows)):
            cr = curve_rows[c]

            # Check switch marker
            is_marker = False
            if switch_events:
                for stick, sdir in switch_events:
                    offset = tick - history
                    if history > 0:
                        col_pos = int((stick - offset) * len(curve_rows) / history)
                        if col_pos == c and 0 <= col_pos < len(curve_rows):
                            is_marker = True
                            break

            if is_marker:
                result.append("\u2506", style="bold yellow")
            elif r == cr:
                # Curve line (thin with slope connectors)
                result.append(line_chars[c], style=f"bold {color}")
            elif r in thr_rows:
                # Threshold line (overrides fill)
                _, ts = thr_rows[r]
                result.append("\u2504", style=ts)
            elif r > cr:
                # Below curve (empty)
                result.append(" ")
            else:
                # Empty above curve
                result.append(" ")

        if r < height - 1:
            result.append("\n")

    # Legend line
    result.append("\n ")
    if thresholds:
        for tv, tl, ts in thresholds:
            result.append(f" \u2504 {tl}={int(tv)}", style=ts)
    if switch_events:
        result.append(" \u2506 Switch", style="bold yellow")

    return result


def _fmt_axis(v: float) -> str:
    """Format axis value compactly."""
    if v >= 10000:
        return f"{v / 1000:.0f}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return f"{v:.0f}"


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


class ChartWidget(Static):
    """Area-style line chart widget."""

    def __init__(self, title: str, data_key: str, color: str = "green",
                 thresholds: list = None, y_max: float = 0, **kwargs):
        super().__init__(**kwargs)
        self.chart_title = title
        self.data_key = data_key
        self.color = color
        self.chart_thresholds = thresholds or []
        self.y_max_override = y_max

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state
        args = app.monitor_args

        data = getattr(state, self.data_key)
        current = data[-1] if data else 0

        # Resolve thresholds dynamically for Concurrency chart
        thresholds = self.chart_thresholds
        y_max = self.y_max_override
        if self.data_key == "hist_active" and state.thresholds_loaded:
            thresholds = []
            if state.up_threshold > 0:
                thresholds.append((state.up_threshold, "UP", "bold red"))
                y_max = state.up_threshold * 4
            if state.down_threshold > 0:
                thresholds.append((state.down_threshold, "DOWN", "bold blue"))
                if y_max == 0:
                    y_max = state.down_threshold * 8

        # Title with current value
        t = Text()
        t.append(f"{self.chart_title}", style=f"bold {self.color}")
        t.append(f"  {self._fmt_val(current)}", style="white")
        t.append("\n")

        # Chart area (subtract title + legend lines)
        chart_h = max(self.size.height - 3, 3)
        chart = render_area_chart(
            data, self.size.width, chart_h,
            y_max=y_max,
            color=self.color,
            thresholds=thresholds if thresholds else None,
            switch_events=state.switch_events,
            tick=state.tick,
            history=args.history,
        )
        t.append(chart)
        return t

    def _fmt_val(self, v: float) -> str:
        if self.data_key == "hist_active":
            return f"{int(v)}"
        elif self.data_key == "hist_thr":
            return f"{v:.1f} t/s"
        elif self.data_key == "hist_ttft":
            return f"{v:.0f} ms"
        elif self.data_key == "hist_tpot":
            return f"{v:.0f} ms"
        return f"{v:.1f}"


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
    layout: grid;
    grid-size: 2 2;
    grid-gutter: 0 1;
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

        with Grid(id="charts"):
            yield ChartWidget(
                "Concurrency (active)",
                "hist_active",
                color="cyan",
                thresholds=[],  # Set dynamically in render
                y_max=0,        # Set dynamically in render
                classes="chart-box",
                id="chart-conc",
            )
            yield ChartWidget(
                "Throughput (out tok/s)",
                "hist_thr",
                color="green",
                y_max=0,
                classes="chart-box",
            )
            yield ChartWidget(
                "TTFT p99 (ms)",
                "hist_ttft",
                color="magenta",
                y_max=0,
                classes="chart-box",
            )
            yield ChartWidget(
                "TPOT mean (ms)",
                "hist_tpot",
                color="yellow",
                y_max=0,
                classes="chart-box",
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
