# E2E test — traffic-triggered TP/PP switch (boot-at-2x2)

Verifies the full causal chain, with no manual switching anywhere:

```
backend boots at tp=2 pp=2 (infinicore)          frontend runs elastic controller
        |                                                  |
        |            benchmark: 20 concurrent chat completions
        |                                                  |
        |                          dynamo_frontend_active_requests rises to ~20
        |                                                  |
        |                       20 > FACTOR_UP(10) x EXPECTED_WORKERS(1)
        |                       for STABLE_POLLS(3) consecutive polls (~6 s)
        |                                                  |
        |<-------- POST /engine/control/switch_parallel_strategy -------------|
        |          {2x2 -> 4x1, request_handling:"wait", admission_handling:"queue"}
        |
        |  new requests park at AsyncLLM.add_request (counted as active!)
        |  in-flight requests drain; switch runs at the idle safe point
        |  parked backlog released onto the 4x1 topology
        |
   state endpoint reports tp=4 pp=1, failed=false; benchmark: 0 lost requests
```

## Files

| File | Purpose |
|---|---|
| `run_e2e.sh` | One-shot orchestrator (boots everything from scratch): stop → boot backend 2x2 → boot frontend+controller → baseline inference → conc=20 load → watch state flip to 4x1 → verify zero lost requests → verdict |
| `m4_run.sh` | M4 against an **already-running** service (booted at 4x1): manual pre-switch down to 2x2 → restart frontend with controller → conc=12 load → controller auto-switches back to 4x1 → **cooldown check** (exactly 1 campaign while load stays high) → zero lost requests. See "M4 standalone" below. |
| `load_gen.py` | Closed-loop load generator (stdlib only). C threads ⇒ in-flight count is exactly C. Prints a JSON summary; exit≠0 if any request failed. Shared by both scripts. |
| `m2_state_fix.sh` | Standalone manual check (no load): switch 4x1↔2x2 via curl and verify the state endpoint + success payloads report the new topology (`is_switching=false`, `pause_routing` validation). Run against any live Dynamo service. |
| `steps/` | The same test split into 4 manual scripts, run in order: `1-backend.sh` (stop old service → infinicore env → boot backend at 2x2 → verify), `2-frontend.sh` (frontend + controller env → verify controller started → model discovery → baseline inference), `3-gen_worload.sh` (background conc=20 load + live gauge cross-check), `4-check-parallelism.sh` (poll state until tp=4 → controller log evidence → verdict). Each script checks its own prereqs and prints the next one. |
| `steps/3-gen_workload_vllm.sh` | Drop-in alternative to `3-gen_worload.sh` that drives load with **`vllm bench serve`**: conc=32, isl=8192, osl=256, num_prompts=256, seed=123 (overrides `VB_CONC/VB_ISL/VB_OSL/VB_NUM_PROMPTS/VB_SEED/VB_FE/BENCH_PLUGIN`). Flags follow `0920/bench_driver.sh` (`--backend openai-chat` → `/v1/chat/completions`; **no** `--random-range-ratio` — the default 0.0 pins exact lengths, verified in `benchmarks/datasets/utils.py:get_sampling_params` and in bench_driver's own W3 results). Runs in background; console + JSON → `/tmp/vllm_bench/i8192_o256_c32_n256.{stdout,json}`. Health check before start, gauge cross-check + `kill -0` guard after 20 s. Exports `VLLM_PLUGINS=infinicore` and `MACA_HOME` itself — without them the `vllm` CLI aborts ("Only one platform plugin can be activated"; `TypeError: stat ... NoneType` from the fork's patched torch cpp_extension). Follow with `4-check-parallelism.sh` as usual. |
| `e2e_run/` | Created per run: `run.log` (full transcript), `load_summary.json`, `load_err.log` |

## Prerequisites

- Branch `ElasticVllm-traffic-switch` checked out in `/workspace/dynamo`, with the
  four files deployed into site-packages (`elastic_controller.py`, patched
  `frontend/main.py`, patched `vllm/handlers.py`). If you re-clone or re-sync,
  re-apply — `service_qwen3.8-27b.sh sync` does **not** copy the frontend package.
- 4 GPUs free.
- Model at `/mnt/nanhuinfer/models/Qwen/Qwen3.8-27B/` (override with `E2E_MODEL`).

## Run

```bash
cd /workspace/dynamo/recipes/elastic-vllm/0920/e2e
./run_e2e.sh                 # full run, ~6 min; leaves the service UP
echo $?                      # 0 = E2E_PASS
cat e2e_run/run.log          # full transcript
cat e2e_run/load_summary.json
```

Overrides: `E2E_CONC=20 E2E_DURATION=200 E2E_FACTOR_UP=10 E2E_GPU_MEM=0.80
E2E_MAX_TOKENS=32 E2E_MODEL=...`

Stop afterwards: `../service_qwen3.8-27b.sh stop`

## M4 standalone (against an already-running service)

`run_e2e.sh` covers the auto-switch chain from scratch. `m4_run.sh` is the M4
scenario proper — it needs a **live** Dynamo service (backend :9091, frontend
:9090) booted at 4x1 under infinicore, and adds the two things the E2E doesn't:
a manual pre-switch *down* to 2x2 (proving the round trip), and an explicit
cooldown check (load stays high 30 s after the switch; exactly one campaign may
run).

```bash
# if nothing is running, start the service first:
../start_dynamo_infinicore.sh          # backend 4x1 + frontend, infinicore env

cd /workspace/dynamo/recipes/elastic-vllm/0920/e2e
./m4_run.sh                            # ~7 min (incl. the idle pre-switch ~26 s)
echo $?                                # 0 = M4_PASS
cat m4_run/run.log                     # full transcript
cat m4_run/load_summary.json
```

Overrides: `M4_CONC=12 M4_DURATION=240 M4_FACTOR_UP=10`.
Verdict line on success:
`M4_PASS: pre-switched to 2x2 -> conc=12 load -> controller auto-switched to 4x1 -> cooldown held -> 0 lost requests`

Last passing M4 run (2026-09-11): controller fired ~6 s after load start
(`SWITCH UP -> 4x1: concurrency 12 > 10 for 3 polls`), campaign
`switched=1 skipped=0 aborted=no elapsed=28.6s`, 1 campaign in 240 s,
720/720 requests ok.

## Manual verification at any point

```bash
# current topology / switch status (the acceptance endpoint)
curl -s -X POST http://localhost:9091/engine/control/parallel_strategy_state \
  -H 'Content-Type: application/json' -d '{}' | jq .
# → {"status":"ok","tensor_parallel_size":4,"pipeline_parallel_size":1,...,
#    "is_switching":false,"failed":false}

# controller decisions
grep -E "SWITCH UP|campaign #" /workspace/dynamo/logs/frontend.log

# live concurrency the controller sees
curl -s http://localhost:9090/metrics | grep '^dynamo_frontend_active_requests'
```

## Pass criteria

1. Backend boots at `tp=2 pp=2` (state endpoint).
2. Baseline inference on 2x2 succeeds.
3. Under conc=20 load, controller logs `SWITCH UP -> 4x1: concurrency 20 > 10 for 3 polls`.
4. State endpoint transitions `is_switching=true` (observable if polling ≤2 s) → `tp=4 pp=1`.
5. Campaign completes (~30 s on this box) and cooldown prevents a second campaign.
6. Benchmark summary: `err=0`, every request HTTP 200. Worst-case latency ≈ switch
   duration (requests parked across the switch); median ≈ steady-state latency.
7. `failed=false` at the end.

## Known environment traps (each one bit us once)

- **`VLLM_PLUGINS=metax` breaks PP=2 inference** on this box: switches report `ok`
  but requests in 2x2 hang, with `FA2 unavailable: libcudart.so.13` spam in
  backend.log. Always use `infinicore` (+ `VLLM_INFINICORE_GDN_SINGLE_STAGE=1`) —
  `run_e2e.sh` sets this itself. `service_qwen3.8-27b.sh` still defaults to metax.
- **`python` is not on PATH in non-interactive ssh shells** (only `python3`).
  Scripts must export `PATH=/opt/conda/bin:$PATH` or use absolute interpreter paths.
- **Readiness checks need a `kill -0` guard**: `service_qwen3.8-27b.sh` prints
  "Backend is ready" even when the process died instantly (false positive seen
  with the two traps above). `run_e2e.sh` checks process liveness first.
- **`physical_world_size` reports `null`** on this engine build. The controller's
  pre-switch bound check degrades gracefully (skips when not an int); the fork
  itself enforces the limit.
- Requests parked during a switch **still count** in `dynamo_frontend_active_requests`,
  so concurrency stays high mid-switch. This is why the controller has campaign
  suppression + cooldown; do not "fix" the metric.
- **Verify your load generator actually generates concurrency.** A shared lock
  accidentally held across the blocking HTTP call serializes all threads: the
  gauge then reads `active_requests 1`, the engine logs `Running: 1, Waiting: 0`,
  and the controller (correctly) never fires — which looks exactly like a
  controller bug. Cross-check with
  `curl -s localhost:9090/metrics | grep ^dynamo_frontend_active_requests`
  while the load runs: it must read ≈ CONC.

## Last passing run (2026-09-11, this box, Qwen3.8-27B, 4 GPUs, infinicore)

`E2E_PASS: booted 2x2 -> conc=20 load -> controller auto-switched to 4x1 -> 0 lost requests`

- Boot verified at `tp=2 pp=2` (num_gpu_blocks 2824); baseline inference on 2x2 OK.
- Controller fired: `SWITCH UP -> 4x1: concurrency 20 > 10 for 3 polls`.
- State endpoint observed `is_switching=true` at t≈7–13 s, then `tp=4 pp=1`.
- Campaign: `switched=1 skipped=0 aborted=no elapsed=26.9s`; cooldown held (1 campaign).
- Benchmark: **420/420 ok, 0 err**; lat med 3.86 s, p95 39.4 s, max 124.3 s
  (the tail is requests parked across the drain+switch window at conc=20 — on a
  2x2 topology the pre-switch queue runs deeper than at 4x1, hence the longer
  tail than the conc=12 runs below).

## Reference numbers

| Metric | Value |
|---|---|
| Boot-to-ready backend (2x2 or 4x1) | ~120 s |
| Switch duration, idle | ~26 s |
| Switch duration, conc=12–20 load | 26–31 s |
| Decision latency (load start → SWITCH UP) | ~6 s (3 polls × 2 s) |
| Lost requests across a switch | 0 / 696 (conc=12), 0 / 720 (conc=12), 0 / 420 (conc=20, boot-2x2) |
| Median latency under conc=12 | ~3.5 s |
| Worst single-request latency | ≈ drain + switch duration (31 s @ conc=12, 124 s @ conc=20 on 2x2) |
