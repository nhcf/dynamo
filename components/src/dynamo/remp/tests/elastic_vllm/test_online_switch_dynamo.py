#!/usr/bin/env python3
"""Dynamo mode online TP/PP switch test for Qwen3.8-27B.

Control plane: http://localhost:9091/engine/control/...
OpenAI API:    http://localhost:9090/v1/chat/completions
"""

import json
import sys
import time
import urllib.request
import urllib.error

CONTROL_BASE = "http://localhost:9091/engine/control"
API_BASE = "http://localhost:9090/v1/chat/completions"
MODEL = "/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/"

SWITCHES = [
    (4, 2, 2),  # 4x1 -> 2x2
    (4, 1, 4),  # 2x2 -> 1x4
    (4, 4, 1),  # 1x4 -> 4x1
]

MAX_SWITCH_WAIT = 300  # seconds


def _post(url, data=None, method="POST"):
    body = json.dumps(data).encode() if data else b"{}"
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()}


def get_parallel_state():
    return _post(f"{CONTROL_BASE}/parallel_strategy_state", method="POST")


def do_switch(new_ws, tp, pp):
    return _post(
        f"{CONTROL_BASE}/switch_parallel_strategy",
        {"new_world_size": new_ws, "target_tensor_parallel_size": tp, "target_pipeline_parallel_size": pp},
    )


def infer(prompt, max_tokens=20):
    data = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    try:
        with urllib.request.urlopen(
            urllib.request.Request(API_BASE, data=json.dumps(data).encode(), method="POST",
                                   headers={"Content-Type": "application/json"}),
            timeout=120,
        ) as resp:
            result = json.loads(resp.read().decode())
            content = result["choices"][0]["message"]["content"]
            tokens = result["usage"]["completion_tokens"]
            return True, content, tokens
    except Exception as e:
        return False, str(e), 0


def wait_for_switch_done(expected_tp, expected_pp):
    """Poll parallel_strategy_state until is_switching=False and TP/PP match."""
    t0 = time.time()
    while time.time() - t0 < MAX_SWITCH_WAIT:
        st = get_parallel_state()
        switching = st.get("is_switching", False)
        failed = st.get("failed", False)
        tp = st.get("tensor_parallel_size", "?")
        pp = st.get("pipeline_parallel_size", "?")
        if failed:
            return False, f"switch failed (tp={tp} pp={pp})", time.time() - t0
        if not switching and tp == expected_tp and pp == expected_pp:
            return True, f"tp={tp} pp={pp}", time.time() - t0
        time.sleep(2)
    st = get_parallel_state()
    return False, f"timeout (tp={st.get('tensor_parallel_size')} pp={st.get('pipeline_parallel_size')} switching={st.get('is_switching')})", time.time() - t0


def main():
    print("=" * 60)
    print("Dynamo Mode Online TP/PP Switch Test — Qwen3.8-27B")
    print("=" * 60)

    # Step 0: verify initial state
    st = get_parallel_state()
    print(f"\n[Step 0] Initial state: TP={st.get('tensor_parallel_size')} PP={st.get('pipeline_parallel_size')} "
          f"is_switching={st.get('is_switching')} failed={st.get('failed')}")
    assert st.get("tensor_parallel_size") == 4 and st.get("pipeline_parallel_size") == 1, \
        f"Unexpected initial state: {st}"

    # Step 1: warmup inference
    print("\n[Step 1] Warmup inference (4×1)...")
    ok, content, tokens = infer("warmup", max_tokens=10)
    print(f"  {'✅' if ok else '❌'} prompt='warmup' → '{content[:50]}' ({tokens} tokens)")
    if not ok:
        print("FATAL: warmup failed")
        sys.exit(1)

    all_pass = True

    for idx, (ws, tp, pp) in enumerate(SWITCHES):
        step = idx * 2 + 2
        label = f"{tp}×{pp}"

        # Switch
        print(f"\n[Step {step}] Switching to {label}...")
        t0 = time.time()
        result = do_switch(ws, tp, pp)
        switch_call_time = time.time() - t0
        print(f"  Switch API returned: status={result.get('status','?')} msg={result.get('message','')[:60]} "
              f"({switch_call_time:.1f}s)")

        # Wait for switch to complete
        ok, msg, wait_time = wait_for_switch_done(tp, pp)
        print(f"  {'✅' if ok else '❌'} Switch to {label}: {msg} (wait={wait_time:.1f}s)")
        if not ok:
            all_pass = False
            continue

        # Verify state
        st = get_parallel_state()
        print(f"  State: TP={st.get('tensor_parallel_size')} PP={st.get('pipeline_parallel_size')} "
              f"blocks={st.get('num_gpu_blocks')} is_switching={st.get('is_switching')}")

        # Inference
        prompts = {
            (2, 2): "hello, who are you?",
            (1, 4): "what is the capital of France?",
            (4, 1): "goodbye!",
        }
        prompt = prompts.get((tp, pp), "hello")
        infer_step = step + 1
        print(f"\n[Step {infer_step}] Inference at {label}...")
        ok, content, tokens = infer(prompt, max_tokens=30)
        print(f"  {'✅' if ok else '❌'} prompt='{prompt}' → '{content[:80]}' ({tokens} tokens)")
        if not ok:
            all_pass = False

    # Summary
    print("\n" + "=" * 60)
    if all_pass:
        print("✅ ALL TESTS PASSED (Dynamo mode)")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 60)
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
