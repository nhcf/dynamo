#!/usr/bin/env python3
"""Closed-loop benchmark load generator (stdlib only).

C threads, each in a closed loop: send a chat completion, wait for the full
response, send the next.  In-flight count is therefore exactly C -- the same
shape the controller's dynamo_frontend_active_requests gauge sees, so "conc=20"
here means the controller reads ~20.

Env: M3_MODEL (required), M3_FE (http://localhost:9090), M3_CONC (20),
     M3_DURATION (200), M3_MAX_TOKENS (32), M3_PROMPT.
Prints one JSON summary line; exit 0 iff zero errors.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

FE = os.environ.get("M3_FE", "http://localhost:9090")
MODEL = os.environ["M3_MODEL"]
CONC = int(os.environ.get("M3_CONC", "20"))
DURATION = float(os.environ.get("M3_DURATION", "200"))
MAX_TOKENS = int(os.environ.get("M3_MAX_TOKENS", "32"))
PROMPT = os.environ.get("M3_PROMPT", "Count from 1 to 20 slowly.")

stop_at = time.monotonic() + DURATION
lock = threading.Lock()
results = []  # (t_start, t_end, status)


def one_request():
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        FE + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            resp.read()
            return (t0, time.time(), f"http_{resp.status}")
    except urllib.error.HTTPError as e:
        return (t0, time.time(), f"http_{e.code}")
    except TimeoutError:
        return (t0, time.time(), "timeout")
    except Exception:
        return (t0, time.time(), "conn")


def worker():
    while time.monotonic() < stop_at:
        r = one_request()          # network call OUTSIDE the lock: true concurrency
        with lock:
            results.append(r)


threads = [threading.Thread(target=worker, daemon=True) for _ in range(CONC)]
t_start = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
wall = time.time() - t_start

ok = sum(1 for _, _, s in results if s == "http_200")
errs = {}
for _, _, s in results:
    if s != "http_200":
        errs[s] = errs.get(s, 0) + 1
lats = sorted(e - b for b, e, s in results if s == "http_200")


def pct(p):
    return round(lats[min(len(lats) - 1, int(p * len(lats)))], 2) if lats else None


print(json.dumps({
    "conc": CONC, "wall_s": round(wall, 1), "total": len(results),
    "ok": ok, "err": len(results) - ok, "err_kinds": errs,
    "lat_min": round(lats[0], 2) if lats else None,
    "lat_med": pct(0.5), "lat_p95": pct(0.95), "lat_max": pct(1.0),
}))
sys.exit(0 if ok and not errs else 1)
