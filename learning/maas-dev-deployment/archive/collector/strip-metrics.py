#!/usr/bin/env python3
"""对提取出的 config.yaml（顶格形态）裁掉 metrics 相关块，保留其余内容与真实凭据。
四个块都以其后第一个"更浅缩进锚点"为终止行：
  receivers.prometheus -> processors:
  processors.resource  -> exporters:
  exporters.prometheusremotewrite -> otlphttp:
  pipelines.metrics    -> telemetry:
"""
import sys

SRC, DST = sys.argv[1], sys.argv[2]

CUTS = [
    ("  prometheus:", "processors:"),
    ("  resource:", "exporters:"),
    ("  prometheusremotewrite:", "  otlphttp:"),
    ("    metrics:", "  telemetry:"),
]

lines = open(SRC).read().splitlines(keepends=True)

for key, term in CUTS:
    try:
        ki = next(i for i, l in enumerate(lines) if l.startswith(key))
    except StopIteration:
        sys.exit(f"key not found: {key!r}")
    try:
        ti = next(i for i in range(ki + 1, len(lines)) if i and lines[i].rstrip("\n") == term)
    except StopIteration:
        sys.exit(f"terminator not found after {key!r}: {term!r}")
    print(f"cut lines {ki+1}..{ti} ({ti-ki} lines): {key.strip()}")
    lines = lines[:ki] + lines[ti:]

open(DST, "w").write("".join(lines))
print(f"written: {DST}")
