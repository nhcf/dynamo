#!/usr/bin/env python3
# demo_load.py — 通用闭环负载生成器
#
# 每 --window 秒输出一行 JSON 到 stdout，供 TUI 或 demo_run.sh 消费。
# 闭环模式: CONC 个线程，每个线程循环 发请求→等完整响应→发下一个 → 在途数恒定=CONC
#
# 用法:
#   python3 demo/demo_load.py --model <model_id> [options]
#
# 参数:
#   --model        模型 ID（必填）
#   --fe-url       frontend 地址（默认 http://localhost:9090）
#   --conc         并发线程数（默认 20）
#   --duration     总运行秒数（默认 200）
#   --input-len    输入 token 长度（生成对应长度的 prompt，默认 128）
#   --output-len   每请求最大输出 token 数（默认 32）
#   --window       统计窗口秒数（默认 5）
#   --tag          场景标签，写入输出 JSON（可选，如 "prefill_heavy"）
#
# 输出格式（每 --window 秒一行）:
#   {"t": 40.0, "tag": "prefill", "conc": 32, "thr_out": 144.0,
#    "ttft_mean": 5398, "ttft_p99": 38288, "tpot_mean": 198,
#    "ok": 192, "err": 0}
#
# 最后一行带有 "final": true 字段，包含全程汇总。

import argparse
import json
import statistics
import threading
import time
from http.client import HTTPConnection
from urllib.parse import urlparse


def parse_args():
    parser = argparse.ArgumentParser(
        description="Closed-loop load generator for Elastic vLLM demo"
    )
    parser.add_argument("--model", required=True, help="Model ID (required)")
    parser.add_argument("--fe-url", default="http://localhost:9090",
                        help="Frontend URL (default: http://localhost:9090)")
    parser.add_argument("--conc", type=int, default=20,
                        help="Concurrent threads (default: 20)")
    parser.add_argument("--duration", type=int, default=200,
                        help="Total run duration in seconds (default: 200)")
    parser.add_argument("--input-len", type=int, default=128,
                        help="Input token length (default: 128)")
    parser.add_argument("--output-len", type=int, default=32,
                        help="Max output tokens per request (default: 32)")
    parser.add_argument("--window", type=int, default=5,
                        help="Stats window in seconds (default: 5)")
    parser.add_argument("--tag", default="", help="Scenario tag (optional)")
    return parser.parse_args()


def send_request(host, port, model, prompt, output_len):
    """Send one streaming completion request; return (ttft_ms, tpot_ms, completion_tokens, ok)."""
    try:
        conn = HTTPConnection(host, port, timeout=300)
        body = json.dumps({
            "model": model,
            "prompt": prompt,
            "max_tokens": output_len,
            "temperature": 0,
            "stream": True,
        })
        t0 = time.monotonic()
        conn.request("POST", "/v1/completions", body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()

        if resp.status != 200:
            resp.read()
            conn.close()
            return (0, 0, 0, False)

        t_first_token = None
        tokens = 0

        while True:
            line = resp.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str or not line_str.startswith("data: "):
                continue
            data_str = line_str[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if t_first_token is None:
                t_first_token = time.monotonic()

            choices = chunk.get("choices", [])
            if choices and choices[0].get("text", ""):
                tokens += 1

        t_end = time.monotonic()
        conn.close()

        if t_first_token is None:
            # No tokens received
            total_ms = (t_end - t0) * 1000
            return (total_ms, 0, 0, False)

        ttft_ms = (t_first_token - t0) * 1000
        gen_time_s = t_end - t_first_token
        tpot_ms = (gen_time_s / max(tokens, 1)) * 1000

        return (ttft_ms, tpot_ms, tokens, True)
    except Exception:
        return (0, 0, 0, False)


def worker(host, port, model, prompt, output_len, start_time, duration,
           stop_event, results_lock, window_results, all_results):
    """Worker thread: closed-loop request sending."""
    end_time = start_time + duration
    while not stop_event.is_set() and time.time() < end_time:
        ttft, tpot, comp_tokens, ok = send_request(host, port, model, prompt, output_len)
        with results_lock:
            window_results.append((ttft, tpot, comp_tokens, ok))
            all_results.append((ttft, tpot, comp_tokens, ok))


def compute_stats(batch, window, tag, conc, start_time, input_len=0, output_len=0):
    """Compute window statistics from a list of result tuples."""
    if not batch:
        return None

    ttfts = [r[0] for r in batch if r[3]]
    tpots = [r[1] for r in batch if r[3]]
    comp_tokens_list = [r[2] for r in batch if r[3]]
    oks = sum(1 for r in batch if r[3])
    errs = sum(1 for r in batch if not r[3])

    total_comp_tokens = sum(comp_tokens_list)
    elapsed = time.time() - start_time
    thr_out = total_comp_tokens / window if oks > 0 else 0

    obj = {
        "t": round(elapsed, 1),
        "tag": tag,
        "conc": conc,
        "input_len": input_len,
        "output_len": output_len,
        "thr_out": round(thr_out, 1),
        "ttft_mean": round(statistics.mean(ttfts), 0) if ttfts else 0,
        "ttft_p99": round(sorted(ttfts)[int(len(ttfts) * 0.99)] if len(ttfts) >= 2 else (ttfts[0] if ttfts else 0), 0),
        "tpot_mean": round(statistics.mean(tpots), 0) if tpots else 0,
        "ok": oks,
        "err": errs,
    }
    return obj


def output_window(results_lock, window_results, all_results, window, tag, conc, start_time, load_args=None):
    """Pop current window results and output JSON."""
    with results_lock:
        batch = window_results[:]
        window_results.clear()
    obj = compute_stats(batch, window, tag, conc, start_time,
                        input_len=load_args.input_len if load_args else 0,
                        output_len=load_args.output_len if load_args else 0)
    if obj:
        print(json.dumps(obj), flush=True)


def main():
    args = parse_args()

    # Generate synthetic prompt (~4 chars per token)
    FILLER = "The quick brown fox jumps over the lazy dog. "
    chars_needed = args.input_len * 4
    prompt = (FILLER * ((chars_needed // len(FILLER)) + 1))[:chars_needed]

    # Parse frontend URL
    parsed = urlparse(args.fe_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 9090

    # Shared state
    results_lock = threading.Lock()
    window_results = []   # [(ttft_ms, tpot_ms, completion_tokens, ok), ...] for current window
    all_results = []      # for final summary
    start_time = time.time()
    stop_event = threading.Event()

    # Start worker threads
    threads = []
    for i in range(args.conc):
        t = threading.Thread(
            target=worker,
            args=(host, port, args.model, prompt, args.output_len,
                  start_time, args.duration, stop_event,
                  results_lock, window_results, all_results),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Collector loop
    try:
        while time.time() - start_time < args.duration:
            time.sleep(args.window)
            output_window(results_lock, window_results, all_results,
                          args.window, args.tag, args.conc, start_time, load_args=args)
    except KeyboardInterrupt:
        pass

    # Final flush
    stop_event.set()
    # Wait briefly for in-flight requests
    time.sleep(1)
    output_window(results_lock, window_results, all_results,
                  args.window, args.tag, args.conc, start_time, load_args=args)

    # Final summary
    if all_results:
        all_ttfts = [r[0] for r in all_results if r[3]]
        all_tpots = [r[1] for r in all_results if r[3]]
        all_comp_tokens = [r[2] for r in all_results if r[3]]
        all_oks = sum(1 for r in all_results if r[3])
        all_errs = sum(1 for r in all_results if not r[3])
        total_time = time.time() - start_time
        total_tokens = sum(all_comp_tokens)

        obj = {
            "t": round(total_time, 1),
            "tag": args.tag,
            "conc": args.conc,
            "input_len": args.input_len,
            "output_len": args.output_len,
            "thr_out": round(total_tokens / total_time, 1) if all_oks > 0 else 0,
            "ttft_mean": round(statistics.mean(all_ttfts), 0) if all_ttfts else 0,
            "ttft_p99": round(sorted(all_ttfts)[int(len(all_ttfts) * 0.99)] if len(all_ttfts) >= 2 else (all_ttfts[0] if all_ttfts else 0), 0),
            "tpot_mean": round(statistics.mean(all_tpots), 0) if all_tpots else 0,
            "ok": all_oks,
            "err": all_errs,
            "final": True,
        }
        print(json.dumps(obj), flush=True)


if __name__ == "__main__":
    main()
