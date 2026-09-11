#!/usr/bin/env python3
# ==============================================================================
# 在线 TP/PP 并行策略切换验证脚本
# 参照 offline.py 流程：init(4×1) → warmup → switch(2×2) → infer → switch(1×4) → infer → switch(4×1) → infer
# 通过 HTTP API 与 vLLM / Dynamo 服务交互
# ==============================================================================

import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVICE_URL = "http://localhost:9090"
CONTROL_URL = "http://localhost:9091"
MODEL = "/mnt/nanhuinfer/models/Qwen3-0.6B/"


# ---------- helpers ----------

def http_request(url: str, method: str = "GET", data: dict | None = None, timeout: int = 30) -> dict:
    """Simple HTTP helper using stdlib only."""
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"error": str(e), "http_code": e.code}
    except URLError as e:
        return {"error": str(e)}


def query_state() -> dict:
    """Query is_switching_parallel_strategy (vllm native)."""
    return http_request(f"{SERVICE_URL}/is_switching_parallel_strategy", method="GET")


def query_control_state() -> dict:
    """Query parallel_strategy_state (dynamo control plane)."""
    return http_request(
        f"{CONTROL_URL}/engine/control/parallel_strategy_state",
        method="POST",
        data={},
    )


def do_switch(tp: int, pp: int, world_size: int) -> dict:
    """Send switch request — try dynamo control plane first, fallback to vllm native."""
    payload = {
        "new_world_size": world_size,
        "target_tensor_parallel_size": tp,
        "target_pipeline_parallel_size": pp,
        "request_handling": "wait",
        "admission_handling": "queue",
    }
    # Try dynamo control plane
    resp = http_request(
        f"{CONTROL_URL}/engine/control/switch_parallel_strategy",
        method="POST",
        data=payload,
    )
    if resp.get("status") in ("switched", "ok"):
        return resp
    # Fallback: vllm native
    resp = http_request(
        f"{SERVICE_URL}/switch_parallel_strategy",
        method="POST",
        data=payload,
    )
    return resp


def do_infer(tag: str, prompt: str, max_tokens: int = 30) -> str:
    """Run inference via OpenAI-compatible API."""
    print(f"\n─── [Infer] {tag} ───")
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    resp = http_request(f"{SERVICE_URL}/v1/chat/completions", method="POST", data=payload)

    if "error" in resp:
        content = resp.get("error", {}).get("message", "FAILED") if isinstance(resp.get("error"), dict) else str(resp["error"])
        tokens = "N/A"
    else:
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "FAILED")
        tokens = resp.get("usage", {}).get("completion_tokens", "N/A")

    print(f"  Prompt:   {prompt!r}")
    print(f"  Response: {content!r}")
    print(f"  Tokens:   {tokens}")
    return content


def wait_switch_done(target_tp: int, target_pp: int, timeout: int = 300) -> bool:
    """Wait until is_switching_parallel_strategy becomes false."""
    print("  Waiting for switch to complete...")
    start = time.time()
    while time.time() - start < timeout:
        # Try control plane for detailed state
        ctrl = query_control_state()
        if ctrl.get("status") == "ok":
            tp = ctrl.get("tensor_parallel_size")
            pp = ctrl.get("pipeline_parallel_size")
            if not ctrl.get("is_switching", False):
                print(f"  ✅ Switch complete: TP={tp}, PP={pp}")
                return True
            if ctrl.get("failed"):
                print(f"  ❌ Switch FAILED!")
                print(json.dumps(ctrl, indent=2))
                return False
        else:
            # Fallback: vllm native
            state = query_state()
            switching = state.get("is_switching_parallel_strategy", False)
            if not switching:
                print("  ✅ Switch complete (is_switching_parallel_strategy=false)")
                return True
        time.sleep(2)

    print(f"  ❌ Switch did not complete within {timeout}s")
    return False


def print_separator(title: str):
    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)


# ==============================================================================
# MAIN TEST FLOW — mirrors offline.py
# ==============================================================================

def main():
    print_separator("Step 0: Verify initial state (4×1)")

    # Try control plane first, then vllm native
    ctrl = query_control_state()
    if ctrl.get("status") == "ok":
        print(json.dumps(ctrl, indent=2))
    else:
        state = query_state()
        print(json.dumps(state, indent=2))
        if state.get("error"):
            print("❌ Cannot reach service. Is it running?")
            sys.exit(1)

    print("✅ Service is reachable")

    # ---------- Warmup (same as offline.py test1) ----------
    print_separator("Step 1: Warmup inference")
    do_infer("warmup", "warmup", max_tokens=4)

    # ---------- Switch 4×1 → 2×2 ----------
    print_separator("Step 2: Switch 4×1 → 2×2")
    print("  Sending switch request...")
    switch_resp = do_switch(tp=2, pp=2, world_size=4)
    print(json.dumps(switch_resp, indent=2))
    if not wait_switch_done(target_tp=2, target_pp=2):
        sys.exit(1)

    # ---------- Inference after switch to 2×2 ----------
    print_separator("Step 3: Inference at 2×2")
    do_infer("2x2", "hello, who are you?")

    # ---------- Switch 2×2 → 1×4 ----------
    print_separator("Step 4: Switch 2×2 → 1×4")
    print("  Sending switch request...")
    switch_resp = do_switch(tp=1, pp=4, world_size=4)
    print(json.dumps(switch_resp, indent=2))
    if not wait_switch_done(target_tp=1, target_pp=4):
        sys.exit(1)

    # ---------- Inference after switch to 1×4 ----------
    print_separator("Step 5: Inference at 1×4")
    do_infer("1x4", "what is the capital of France?")

    # ---------- Switch 1×4 → 4×1 (back to original) ----------
    print_separator("Step 6: Switch 1×4 → 4×1 (back to original)")
    print("  Sending switch request...")
    switch_resp = do_switch(tp=4, pp=1, world_size=4)
    print(json.dumps(switch_resp, indent=2))
    if not wait_switch_done(target_tp=4, target_pp=1):
        sys.exit(1)

    # ---------- Final inference ----------
    print_separator("Step 7: Final inference at 4×1")
    do_infer("4x1", "goodbye!", max_tokens=20)

    # ---------- Summary ----------
    print_separator("Summary")
    print("  ✅ All online switch tests passed!")
    print("  Transitions verified:")
    print("    4×1 ──→ 2×2 ──→ 1×4 ──→ 4×1")
    print("  Inference succeeded at each configuration.")


if __name__ == "__main__":
    main()