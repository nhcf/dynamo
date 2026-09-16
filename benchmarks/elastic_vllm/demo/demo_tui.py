#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# demo_tui.py — Elastic TP/PP Switch Monitor TUI (Textual + Rich)
# Pure observer — never triggers a switch call
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
    parser.add_argument("--interval", type=int, default=2,
                        help="Polling interval in seconds (default: 2)")
    parser.add_argument("--history", type=int, default=120,
                        help="Chart history length in data points (default: 120)")
    parser.add_argument("--up-threshold", type=int, default=10,
                        help="UP threshold line (default: 10)")
    parser.add_argument("--down-threshold", type=int, default=2,
                        help="DOWN threshold line (default: 2)")
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

    # History
    hist_active: deque = field(default_factory=deque)
    hist_thr: deque = field(default_factory=deque)
    hist_tpot: deque = field(default_factory=deque)

    # Switch events: list of (tick, direction)
    switch_events: list = field(default_factory=list)

    # Event log (max 100 entries)
    event_log: list = field(default_factory=list)

    # Tick
    tick: int = 0
    start_time: float = field(default_factory=time.time)

    # File read positions
    stats_pos: int = 0
    events_pos: int = 0
    log_pos: int = 0


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
        add_event(state, f"[CONTROLLER] SWITCH {direction} -> {new_tp}x{new_pp}")

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
        if re.search(r"SWITCH (UP|DOWN)", line):
            msg = re.search(r"SWITCH (UP|DOWN).*", line)
            if msg:
                add_event(state, f"[CONTROLLER] {msg.group(0)}")
        elif re.search(r"campaign.*finished", line, re.IGNORECASE):
            msg = re.search(r"\[TP/PP\] campaign.*", line, re.IGNORECASE)
            if msg:
                state.campaigns += 1
                add_event(state, f"[CONTROLLER] {msg.group(0)}")
        elif "[TP/PP] controller started" in line:
            add_event(state, "[CONTROLLER] Elastic controller started")


def add_event(state: State, msg: str):
    elapsed = format_elapsed(state)
    state.event_log.append(f"{elapsed} {msg}")
    if len(state.event_log) > 100:
        state.event_log = state.event_log[-100:]


def update_history(state: State, history: int):
    state.hist_active.append(state.active_requests)
    state.hist_thr.append(state.thr_out)
    state.hist_tpot.append(state.tpot_mean)
    while len(state.hist_active) > history:
        state.hist_active.popleft()
        state.hist_thr.popleft()
        state.hist_tpot.popleft()


def poll_all(state: State, args):
    fetch_topology(state, args.ctrl_url)
    fetch_metrics(state, args.fe_url)
    read_stats_file(state, args.stats_file)
    read_events_file(state, args.events_file)
    read_log_events(state, args.log_file)
    update_history(state, args.history)
    state.tick += 1


# ===================== Formatting Helpers =====================

def format_elapsed(state: State) -> str:
    diff = int(time.time() - state.start_time)
    return f"{diff // 60:02d}:{diff % 60:02d}"


def format_delta(curr: float, prev: float, unit: str = "", inverse: bool = False) -> str:
    delta = int(round(curr)) - int(round(prev))
    if delta > 0:
        return f"\u25b2 +{delta}{unit}"
    elif delta < 0:
        return f"\u25bc {delta}{unit}"
    else:
        return f"  0{unit}"


# ===================== Sparkline Rendering (Rich Text) =====================

SPARK_CHARS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def render_sparkline(data: deque, switch_events: list,
                     up_thr: int, down_thr: int,
                     max_override: int, tick: int, history: int,
                     width: int = 80) -> Text:
    """Render a sparkline chart as Rich Text with threshold lines and switch markers."""
    n = len(data)
    if n < 2 or width < 10:
        return Text("  (waiting for data...)", style="dim")

    # Reserve space for y-axis labels
    y_width = 5
    chart_width = max(width - y_width - 2, 8)

    # Downsample to chart_width points
    data_list = list(data)
    samples = []
    if n <= chart_width:
        samples = [float(v) for v in data_list]
    else:
        bucket = (n + chart_width - 1) // chart_width
        for i in range(chart_width):
            start = i * bucket
            end = min(start + bucket, n)
            if start >= n:
                samples.append(0.0)
            else:
                chunk = data_list[start:end]
                samples.append(sum(float(v) for v in chunk) / len(chunk))

    sample_n = len(samples)
    if sample_n < 2:
        return Text("  (waiting for data...)", style="dim")

    # Y range
    y_max = max(int(round(v)) for v in samples)
    if max_override > y_max:
        y_max = max_override
    if y_max < 1:
        y_max = 1

    # Chart height in rows (8 = full block height)
    chart_height = 8

    # Build row-based rendering
    rows = []
    for row in range(chart_height):
        row_val = y_max * (chart_height - row) / chart_height
        next_val = y_max * (chart_height - row - 1) / chart_height

        parts = Text()

        # Y-axis label for top, middle, bottom rows
        if row == 0:
            parts.append(f"{y_max:4d}\u2507", style="dim")
        elif row == chart_height // 2:
            parts.append(f"{y_max // 2:4d}\u2507", style="dim")
        elif row == chart_height - 1:
            parts.append(f"   0\u2507", style="dim")
        else:
            parts.append("    \u2507", style="dim")

        # Build chart line using Unicode block characters
        for c in range(min(chart_width, sample_n)):
            val = samples[c]

            # Check switch event at this column
            col_has_switch = False
            switch_dir = ""
            for stick, sdir in switch_events:
                offset = tick - history
                rel = stick - offset
                col_pos = rel * chart_width // history
                if col_pos == c and 0 <= col_pos < chart_width:
                    col_has_switch = True
                    switch_dir = sdir
                    break

            if col_has_switch:
                parts.append("\u2508", style="bold yellow")
                continue

            # Check threshold lines
            up_row = chart_height - 1 - (up_thr * (chart_height - 1) // y_max) if up_thr > 0 else -1
            down_row = chart_height - 1 - (down_thr * (chart_height - 1) // y_max) if down_thr > 0 else -1

            if row == up_row or row == down_row:
                # Check if data point is also here
                frac = val / y_max if y_max > 0 else 0
                filled = frac * chart_height
                block_start = chart_height - filled
                if block_start <= row < block_start + 1:
                    parts.append(SPARK_CHARS[8], style="bold green")
                else:
                    parts.append("\u2504", style="white")
                continue

            # Data sparkline — use block characters proportional to value
            frac = val / y_max if y_max > 0 else 0
            filled = frac * chart_height
            block_start = chart_height - filled

            if row > block_start:
                # Fully filled row
                parts.append(SPARK_CHARS[8], style="green")
            elif row + 1 > block_start and row < block_start + 1:
                # Partially filled row
                parts.append(SPARK_CHARS[8], style="green")
            # else: empty, skip (Text already empty)

        rows.append(parts)

    # Build final text by reversing (top to bottom)
    result = Text()
    for i, row_text in enumerate(rows):
        result.append(row_text)
        if i < len(rows) - 1:
            result.append("\n")

    # Add threshold labels
    result.append("\n")
    if up_thr > 0:
        result.append(f"  \u25bd UP={up_thr}  ", style="bold red")
    if down_thr > 0:
        result.append(f"  \u25b3 DOWN={down_thr}", style="bold blue")

    return result


# ===================== Textual Widgets =====================

class TopologyPanel(Static):
    """Topology status panel."""

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state

        t = Text()
        t.append("Topology\n", style="bold cyan")
        t.append("──────────────────────────\n", style="dim")

        # TP/PP
        t.append(f"  TP={state.tp}  PP={state.pp}   ")

        if state.is_switching:
            t.append(state.strategy_label, style="bold yellow blink")
        elif state.failed:
            t.append("[FAILED]", style="bold red")
        else:
            t.append(state.strategy_label, style="bold green")

        t.append("\n")

        # Switching
        t.append("  Switching: ")
        if state.is_switching:
            t.append("YES", style="bold yellow")
        else:
            t.append("NO ", style="bold green")
        t.append("\n")

        # Failed
        t.append("  Failed:    ")
        if state.failed:
            t.append("YES", style="bold red")
        else:
            t.append("NO ", style="bold green")
        t.append("\n")

        # Campaigns
        t.append(f"  Campaigns: {state.campaigns}\n")

        return t


class MetricsPanel(Static):
    """Metrics snapshot panel."""

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state

        t = Text()
        t.append("Metrics Snapshot\n", style="bold cyan")
        t.append("─────────────────────────────────────────────────────\n", style="dim")

        # Header
        t.append(f"  {'Active':<10} {'Throughput':<14} {'TTFT(p99)':<14} {'TPOT(mean)':<14} {'OK%':<6}\n", style="bold white")

        # Values
        t.append(f"  {int(state.active_requests):<10} ")
        t.append(f"{state.thr_out:.1f} t/s{'':<6} ")
        t.append(f"{state.ttft_p99:.0f} ms{'':<6} ")
        t.append(f"{state.tpot_mean:.0f} ms{'':<6} ")
        t.append(f"{state.ok_pct}%")
        t.append("\n")

        # Deltas
        d_active = format_delta(state.active_requests, state.prev_active)
        d_thr = format_delta(state.thr_out, state.prev_thr_out, " t/s")
        d_ttft = format_delta(state.ttft_p99, state.prev_ttft_p99, " ms", inverse=True)
        d_tpot = format_delta(state.tpot_mean, state.prev_tpot_mean, " ms", inverse=True)

        t.append(f"  {d_active:<10} ", style="yellow")
        t.append(f"{d_thr:<14} ", style="green")

        ttft_style = "green" if state.ttft_p99 < state.prev_ttft_p99 else "red"
        t.append(f"{d_ttft:<14} ", style=ttft_style)

        tpot_style = "green" if state.tpot_mean < state.prev_tpot_mean else "red"
        t.append(f"{d_tpot:<14} ", style=tpot_style)

        return t


class ChartWidget(Static):
    """A sparkline chart widget with threshold lines and switch markers."""

    def __init__(self, title: str, data_key: str,
                 up_thr: int = 0, down_thr: int = 0,
                 max_override: int = 0, **kwargs):
        super().__init__(**kwargs)
        self.chart_title = title
        self.data_key = data_key
        self.up_thr = up_thr
        self.down_thr = down_thr
        self.max_override = max_override

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state
        args = app.monitor_args

        data = getattr(state, self.data_key)
        w = self.size.width

        t = Text()
        t.append(self.chart_title, style="bold cyan")
        t.append("\n")

        chart = render_sparkline(
            data, state.switch_events,
            self.up_thr, self.down_thr,
            self.max_override, state.tick, args.history,
            width=max(w, 40)
        )
        t.append(chart)
        return t


class EventLogPanel(Static):
    """Scrollable event log panel."""

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state

        t = Text()
        t.append("Event Log\n", style="bold cyan")
        t.append("─────────────────────────────────────────────────────\n", style="dim")

        # Show last 8 events
        events = state.event_log[-8:]
        for ev in events:
            if "[CONTROLLER]" in ev:
                t.append(f"  {ev}\n", style="yellow")
            elif "[LOAD]" in ev:
                t.append(f"  {ev}\n", style="cyan")
            elif re.search(r"error|fail", ev, re.IGNORECASE):
                t.append(f"  {ev}\n", style="bold red")
            else:
                t.append(f"  {ev}\n", style="white")

        return t


class StatusBar(Static):
    """Bottom status bar."""

    def render(self) -> Text:
        app = self.app
        state = app.monitor_state
        elapsed = format_elapsed(state)

        t = Text()
        t.append(f"  Tick: {state.tick:<4d}  |  Elapsed: {elapsed}  |  ", style="bold cyan")
        t.append("q", style="bold white")
        t.append("=quit  ", style="cyan")
        return t


# ===================== Main App =====================

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

#topology-panel {
    width: 1fr;
    height: 100%;
    border: round $cyan;
    padding: 0 1;
    margin-right: 1;
}

#metrics-panel {
    width: 2fr;
    height: 100%;
    border: round $cyan;
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
    border: round $cyan;
    padding: 0 1;
    margin-bottom: 1;
}

#event-log {
    height: 10;
    border: round $cyan;
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
            yield TopologyPanel(id="topology-panel")
            yield MetricsPanel(id="metrics-panel")

        with Vertical(id="charts"):
            yield ChartWidget(
                "Concurrency (active_requests)",
                "hist_active",
                up_thr=self.monitor_args.up_threshold,
                down_thr=self.monitor_args.down_threshold,
                max_override=40,
                classes="chart-box",
            )
            yield ChartWidget(
                "Throughput (tok/s)",
                "hist_thr",
                max_override=200,
                classes="chart-box",
            )
            yield ChartWidget(
                "TPOT mean (ms)",
                "hist_tpot",
                max_override=500,
                classes="chart-box",
            )

        yield EventLogPanel(id="event-log")
        yield StatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        # Initial poll
        poll_all(self.monitor_state, self.monitor_args)
        # Set up periodic polling
        self.set_interval(self.monitor_args.interval, self._poll_and_refresh)

    def _poll_and_refresh(self) -> None:
        """Poll all data sources and refresh all widgets."""
        poll_all(self.monitor_state, self.monitor_args)
        # Refresh all Static widgets by calling their render
        for widget in self.query(Static):
            widget.refresh()


# ===================== Entry Point =====================

def run():
    args = parse_args()
    app = ElasticMonitorApp(args)
    app.run()


if __name__ == "__main__":
    run()
