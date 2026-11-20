#!/usr/bin/env python3
# ==============================================================================
# 离线 TP/PP 并行策略切换验证脚本
# 使用 LLM 类进行离线推理 + 切换，对应原 offline.py
# ==============================================================================

import argparse
import sys

from vllm import LLM, SamplingParams
from vllm.v1.engine import SwitchParallelStrategyRequest


def parse_args():
    parser = argparse.ArgumentParser(
        description="Offline TP/PP parallel-strategy switch test"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="/mnt/nanhuinfer/models/Qwen3-0.6B/",
        help="Model path (default: Qwen3-0.6B)",
    )
    parser.add_argument(
        "--gpu-memory-utilization",
        type=float,
        default=0.85,
        help="GPU memory utilization (default: 0.85)",
    )
    return parser.parse_args()


def print_separator(title: str):
    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)


def main():
    args = parse_args()

    print_separator("Step 1: Initialize LLM (4×1)")
    llm = LLM(
        model=args.model,
        tensor_parallel_size=4,
        pipeline_parallel_size=1,
        tp_pp_switch_prebuild_strategies=["4x1", "2x2", "1x4"],
        tp_pp_switch_kv_transfer_window_size=2,
        tp_pp_switch_kv_transfer_max_scratch_size_mb=256,
        enforce_eager=True,
        gpu_memory_utilization=args.gpu_memory_utilization,
    )

    # ---------- Warmup ----------
    print_separator("Step 2: Warmup inference")
    warmup_out = llm.generate(["warmup"], SamplingParams(max_tokens=4))
    for o in warmup_out:
        print(f"  Prompt: {o.prompt!r}  →  {o.outputs[0].text!r}")

    # ---------- Switch 4×1 → 2×2 ----------
    print_separator("Step 3: Switch 4×1 → 2×2")
    llm.llm_engine.switch_parallel_strategy(
        SwitchParallelStrategyRequest(
            new_world_size=4,
            target_tensor_parallel_size=2,
            target_pipeline_parallel_size=2,
        )
    )
    print("  ✅ Switch to 2×2 completed")

    # ---------- Inference at 2×2 ----------
    print_separator("Step 4: Inference at 2×2")
    output = llm.generate(["hello"], SamplingParams(max_tokens=100))
    for o in output:
        print(f"  Prompt: {o.prompt!r}")
        print(f"  Text:   {o.outputs[0].text!r}")

    # ---------- Switch 2×2 → 1×4 ----------
    print_separator("Step 5: Switch 2×2 → 1×4")
    llm.llm_engine.switch_parallel_strategy(
        SwitchParallelStrategyRequest(
            new_world_size=4,
            target_tensor_parallel_size=1,
            target_pipeline_parallel_size=4,
        )
    )
    print("  ✅ Switch to 1×4 completed")

    # ---------- Inference at 1×4 ----------
    print_separator("Step 6: Inference at 1×4")
    output = llm.generate(["what is the capital of France?"], SamplingParams(max_tokens=100))
    for o in output:
        print(f"  Prompt: {o.prompt!r}")
        print(f"  Text:   {o.outputs[0].text!r}")

    # ---------- Switch 1×4 → 4×1 ----------
    print_separator("Step 7: Switch 1×4 → 4×1 (back to original)")
    llm.llm_engine.switch_parallel_strategy(
        SwitchParallelStrategyRequest(
            new_world_size=4,
            target_tensor_parallel_size=4,
            target_pipeline_parallel_size=1,
        )
    )
    print("  ✅ Switch to 4×1 completed")

    # ---------- Final inference ----------
    print_separator("Step 8: Final inference at 4×1")
    output = llm.generate(["goodbye!"], SamplingParams(max_tokens=100))
    for o in output:
        print(f"  Prompt: {o.prompt!r}")
        print(f"  Text:   {o.outputs[0].text!r}")

    # ---------- Summary ----------
    print_separator("Summary")
    print("  ✅ All offline switch tests passed!")
    print("  Transitions verified:")
    print("    4×1 ──→ 2×2 ──→ 1×4 ──→ 4×1")
    print("  Inference succeeded at each configuration.")


if __name__ == "__main__":
    main()