# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Traffic-triggered rolling TP/PP switch controller.

Watches frontend request concurrency and, when it crosses a configured
watermark, drives a *rolling* ElasticVllm TP/PP strategy switch across the
configured backend workers -- one worker at a time, in configured order, until
every worker is on the target strategy.

Why this lives in the frontend process
--------------------------------------
The frontend is the only component that observes aggregate client concurrency
(``dynamo_frontend_active_requests``), and the only one that outlives an
individual worker's reconfiguration.  A worker cannot see the fleet; the router
cannot issue engine-control calls.

Why no request is ever terminated
---------------------------------
The switch is issued with ``request_handling="wait"`` and
``admission_handling="queue"``.  The engine then (a) blocks new requests at
``AsyncLLM.add_request``, (b) lets in-flight requests finish, (c) performs the
switch at the idle safe point, and (d) releases the parked backlog onto the new
topology.  The worker handler's ``await`` returns only *after* the switch
completes -- ElasticVllm defers the utility response until the drain future
resolves -- so a campaign needs no completion polling.

The feedback hazard
-------------------
Requests parked at ``add_request`` during a switch are still counted by
``dynamo_frontend_active_requests``, so concurrency is *guaranteed* to rise
while a switch is in flight.  A naive controller re-triggers on its own action.
Four independent guards are therefore load-bearing, not belt-and-braces:
``campaign_active`` suppression, a post-campaign ``cooldown_s``, ``stable_polls``
hysteresis, and a **configured** ``expected_workers`` denominator that never
tracks the live instance count.

Hard dependency rule for this module
------------------------------------
Standard library only.  No ``vllm``, no ``dynamo.vllm.handlers``, no third-party
HTTP client.  Being inside the frontend package is not a licence to import engine
code: this rule is what keeps the module unit-testable with no GPU and no engine
installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Control-plane routes
#
# Engine control routes are registered by the vLLM worker component and served
# HTTP-only by the worker's system-status server on DYN_SYSTEM_PORT.  They are
# deliberately *not* published to discovery (the system-status address is stored
# locally and never registered), which is why `worker_urls` below must be
# configured rather than discovered.
# ---------------------------------------------------------------------------
CONTROL_STATE_PATH = "/engine/control/parallel_strategy_state"
CONTROL_SWITCH_PATH = "/engine/control/switch_parallel_strategy"

# Metric names, from lib/runtime/src/metrics/prometheus_names.rs
# (name_prefix::FRONTEND = "dynamo_frontend", frontend_service::ACTIVE_REQUESTS
# = "active_requests").  Both gauges are labelled ["model"].
ACTIVE_REQUESTS_METRIC = "dynamo_frontend_active_requests"
INFLIGHT_REQUESTS_METRIC = "dynamo_frontend_inflight_requests"

ENV_PREFIX = "DYN_ELASTIC_SWITCH_"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
_STRATEGY_RE = re.compile(r"^\s*(\d+)\s*[xX*]\s*(\d+)\s*$")


@dataclass(frozen=True)
class Strategy:
    """A TP/PP topology, e.g. ``4x1`` == TP=4, PP=1."""

    tp: int
    pp: int

    @property
    def world_size(self) -> int:
        return self.tp * self.pp

    @classmethod
    def parse(cls, text: str) -> "Strategy":
        """Parse ``"4x1"`` / ``"4X1"`` / ``"2*2"``.  Raises ``ValueError``.

        Parsed at startup, never mid-campaign: a malformed target must fail the
        process before it can fail a switch.
        """
        match = _STRATEGY_RE.match(text or "")
        if not match:
            raise ValueError(
                f"invalid strategy {text!r}; expected '<tp>x<pp>', e.g. '4x1'"
            )
        tp, pp = int(match.group(1)), int(match.group(2))
        if tp < 1 or pp < 1:
            raise ValueError(f"invalid strategy {text!r}: tp and pp must be >= 1")
        return cls(tp=tp, pp=pp)

    def as_tuple(self) -> tuple[int, int]:
        return (self.tp, self.pp)

    def __str__(self) -> str:
        return f"{self.tp}x{self.pp}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ControllerConfig:
    """All controller knobs.  See ``from_env`` for the env surface."""

    enabled: bool = False
    model: str | None = None
    metrics_url: str = "http://127.0.0.1:9090/metrics"
    worker_urls: tuple[str, ...] = ()
    expected_workers: int = 1
    poll_interval_s: float = 2.0
    concurrency_factor_up: float = 10.0
    concurrency_factor_down: float | None = None
    stable_polls: int = 3
    cooldown_s: float = 60.0
    strategy_up: Strategy = field(default_factory=lambda: Strategy(4, 1))
    strategy_down: Strategy | None = None
    pause_routing: bool = False
    switch_timeout_s: float = 300.0
    drain_timeout_s: float | None = None
    http_timeout_s: float = 10.0
    metric_name: str = ACTIVE_REQUESTS_METRIC

    def validate(self) -> None:
        """Fail fast on a configuration that cannot work.  Raises ``ValueError``."""
        if not self.enabled:
            return
        if not self.worker_urls:
            raise ValueError(
                f"{ENV_PREFIX}WORKER_URLS is required when the controller is enabled"
            )
        if self.expected_workers < 1:
            raise ValueError(f"{ENV_PREFIX}EXPECTED_WORKERS must be >= 1")
        if self.concurrency_factor_up <= 0:
            raise ValueError(f"{ENV_PREFIX}FACTOR_UP must be > 0")
        if (
            self.concurrency_factor_down is not None
            and self.concurrency_factor_down >= self.concurrency_factor_up
        ):
            raise ValueError(
                f"{ENV_PREFIX}FACTOR_DOWN ({self.concurrency_factor_down}) must be "
                f"below {ENV_PREFIX}FACTOR_UP ({self.concurrency_factor_up}); "
                "a single watermark oscillates"
            )
        if self.concurrency_factor_down is not None and self.strategy_down is None:
            raise ValueError(
                f"{ENV_PREFIX}FACTOR_DOWN is set but {ENV_PREFIX}STRATEGY_DOWN is not"
            )
        if self.stable_polls < 1:
            raise ValueError(f"{ENV_PREFIX}STABLE_POLLS must be >= 1")
        if self.poll_interval_s <= 0:
            raise ValueError(f"{ENV_PREFIX}POLL_INTERVAL_S must be > 0")
        if len(self.worker_urls) != self.expected_workers:
            # Not fatal -- expected_workers is the *policy* denominator and may
            # deliberately differ from the number of reachable URLs -- but it is
            # almost always a typo, so say so once at startup.
            logger.warning(
                "[TP/PP] %sEXPECTED_WORKERS=%d but %d worker URL(s) configured; "
                "the denominator stays at %d",
                ENV_PREFIX,
                self.expected_workers,
                len(self.worker_urls),
                self.expected_workers,
            )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ControllerConfig":
        """Build a config from ``DYN_ELASTIC_SWITCH_*``.  Disabled unless asked.

        ``env`` is injectable for tests; defaults to ``os.environ``.
        """
        env = os.environ if env is None else env

        def get(name: str, default: str = "") -> str:
            return (env.get(ENV_PREFIX + name) or default).strip()

        def get_bool(name: str, default: bool) -> bool:
            raw = get(name)
            if not raw:
                return default
            return raw.lower() in ("1", "true", "yes", "on")

        def get_float(name: str, default: float) -> float:
            raw = get(name)
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                raise ValueError(f"{ENV_PREFIX}{name}={raw!r} is not a number") from None

        def get_opt_float(name: str) -> float | None:
            raw = get(name)
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                raise ValueError(f"{ENV_PREFIX}{name}={raw!r} is not a number") from None

        def get_int(name: str, default: int) -> int:
            raw = get(name)
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                raise ValueError(f"{ENV_PREFIX}{name}={raw!r} is not an integer") from None

        # Worker URLs are base URLs (we append the control path); metrics_url is
        # a complete URL and is used verbatim.
        urls = tuple(u.strip().rstrip("/") for u in get("WORKER_URLS").split(",") if u.strip())
        model = get("MODEL") or None

        strategy_down_raw = get("STRATEGY_DOWN")
        cfg = cls(
            enabled=get_bool("ENABLE", False),
            model=model,
            metrics_url=get("METRICS_URL", "http://127.0.0.1:9090/metrics"),
            worker_urls=urls,
            expected_workers=get_int("EXPECTED_WORKERS", max(1, len(urls))),
            poll_interval_s=get_float("POLL_INTERVAL_S", 2.0),
            concurrency_factor_up=get_float("FACTOR_UP", 10.0),
            concurrency_factor_down=get_opt_float("FACTOR_DOWN"),
            stable_polls=get_int("STABLE_POLLS", 3),
            cooldown_s=get_float("COOLDOWN_S", 60.0),
            strategy_up=Strategy.parse(get("STRATEGY_UP", "4x1")),
            strategy_down=Strategy.parse(strategy_down_raw) if strategy_down_raw else None,
            pause_routing=get_bool("PAUSE_ROUTING", False),
            switch_timeout_s=get_float("TIMEOUT_S", 300.0),
            drain_timeout_s=get_opt_float("DRAIN_TIMEOUT_S"),
            http_timeout_s=get_float("HTTP_TIMEOUT_S", 10.0),
            metric_name=get("METRIC_NAME", ACTIVE_REQUESTS_METRIC),
        )
        cfg.validate()
        return cfg


# ---------------------------------------------------------------------------
# Prometheus text parsing (module-level and pure, so tests need no controller)
# ---------------------------------------------------------------------------
_METRIC_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>\S+)(?:\s+\S+)?$"
)
_MODEL_LABEL_RE = re.compile(r'(?:^|,)\s*model\s*=\s*"((?:[^"\\]|\\.)*)"')


def _unquote_label(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")


def sum_metric(text: str, metric_name: str, model: str | None = None) -> int:
    """Sum a gauge's samples out of a Prometheus text exposition.

    Returns 0 when the series is absent (a frontend that has served no requests
    yet publishes no sample at all, and "no data" means "no load", not "unknown"
    -- a scrape *failure* is the unknown case and is reported by the caller).

    ``model=None`` sums every ``model`` label value; otherwise only matching
    series are counted.  Non-finite samples (``NaN``/``+Inf``) are ignored.
    """
    total = 0.0
    found = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _METRIC_LINE_RE.match(line)
        if not match or match.group("name") != metric_name:
            continue
        labels = match.group("labels")
        if model is not None:
            label_match = _MODEL_LABEL_RE.search(labels) if labels else None
            if label_match is None or _unquote_label(label_match.group(1)) != model:
                continue
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        if value != value or value in (float("inf"), float("-inf")):  # NaN / Inf
            continue
        total += value
        found = True
    if not found:
        return 0
    return int(total)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WorkerOutcome:
    """Result of attempting one worker.  ``abort`` stops the whole campaign."""

    url: str
    action: str  # switched|skipped|aborted
    reason: str
    abort: bool = False
    elapsed_s: float | None = None


@dataclass(frozen=True)
class CampaignResult:
    switched: int
    skipped: int
    aborted_reason: str | None
    elapsed_s: float
    outcomes: tuple[WorkerOutcome, ...]

    @property
    def ok(self) -> bool:
        return self.aborted_reason is None


@dataclass(frozen=True)
class Decision:
    action: str  # switch_up|switch_down|noop
    concurrency: int | None
    threshold: float
    streak: int
    target: Strategy | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# HTTP helpers (sync; always called through asyncio.to_thread)
# ---------------------------------------------------------------------------
def _http_json(
    url: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET (or POST when ``payload`` is given) and decode a JSON object.

    Raises ``OSError``/``ValueError`` on transport or decode failure.  A non-2xx
    response is *not* automatically an error here: the control routes answer 200
    with ``{"status": "error"|"conflict"|...}``, and the caller branches on that.
    """
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:  # non-2xx: still try to read the JSON body
        body = exc.read()
        if not body:
            raise
    if not body:
        raise ValueError(f"empty response from {url}")
    decoded = json.loads(body.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError(f"{url} returned {type(decoded).__name__}, expected an object")
    return decoded


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class ElasticSwitchController:
    """Poll concurrency, evaluate the policy, run a rolling campaign.

    One instance per frontend process.  ``run()`` loops until cancelled.
    """

    def __init__(self, cfg: ControllerConfig) -> None:
        self.cfg = cfg
        self._streak_up = 0
        self._streak_down = 0
        self._campaign_active = False
        self._last_campaign_end: float | None = None
        self._campaigns_run = 0

    # -- public ---------------------------------------------------------
    async def run(self) -> None:
        """Poll loop.  Runs until the task is cancelled."""
        cfg = self.cfg
        if not cfg.enabled:
            logger.info("[TP/PP] controller disabled; not starting")
            return
        logger.info(
            "[TP/PP] controller started: metric=%s model=%s url=%s workers=%d "
            "expected_workers=%d up=%s@%.1fx down=%s@%s stable_polls=%d "
            "cooldown=%.0fs poll=%.1fs pause_routing=%s timeout=%.0fs",
            cfg.metric_name,
            cfg.model or "*",
            cfg.metrics_url,
            len(cfg.worker_urls),
            cfg.expected_workers,
            cfg.strategy_up,
            cfg.concurrency_factor_up,
            cfg.strategy_down or "-",
            (
                f"{cfg.concurrency_factor_down:.1f}x"
                if cfg.concurrency_factor_down is not None
                else "-"
            ),
            cfg.stable_polls,
            cfg.cooldown_s,
            cfg.poll_interval_s,
            cfg.pause_routing,
            cfg.switch_timeout_s,
        )
        while True:
            try:
                decision = await self._poll_once()
                if decision.action != "noop":
                    await self._run_campaign(decision.target)  # type: ignore[arg-type]
            except asyncio.CancelledError:
                logger.info("[TP/PP] controller cancelled; exiting")
                raise
            except Exception:
                # One bad poll must not kill the task: an asyncio task that dies
                # is silent until GC, and a dead controller looks exactly like
                # "traffic never crossed the threshold".
                logger.exception("[TP/PP] poll iteration failed; continuing")
            await asyncio.sleep(cfg.poll_interval_s)

    # -- signal ---------------------------------------------------------
    def _scrape_concurrency(self) -> int | None:
        """Sync scrape.  ``None`` means "no sample" (transport failure)."""
        try:
            text = self._fetch_metrics()
        except Exception as exc:
            logger.warning("[TP/PP] metrics scrape failed (%s); skipping poll", exc)
            return None
        return sum_metric(text, self.cfg.metric_name, self.cfg.model)

    def _fetch_metrics(self) -> str:
        request = urllib.request.Request(self.cfg.metrics_url, headers={"Accept": "text/plain"})
        with urllib.request.urlopen(request, timeout=self.cfg.http_timeout_s) as response:
            return response.read().decode("utf-8", errors="replace")

    # Thin seams so tests can drive the controller with no sockets.  Keep these
    # sync: they are always called through asyncio.to_thread.
    def _get_json(self, url: str, timeout: float) -> dict[str, Any]:
        return _http_json(url, timeout)

    def _post_json(self, url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        return _http_json(url, timeout, payload)

    # -- policy ---------------------------------------------------------
    async def _poll_once(self) -> Decision:
        cfg = self.cfg
        concurrency = await asyncio.to_thread(self._scrape_concurrency)
        up_threshold = cfg.concurrency_factor_up * cfg.expected_workers

        if concurrency is None:
            # A metrics blip must not silently cancel an impending switch, but it
            # must not count as a sample either: leave both streaks untouched.
            return Decision("noop", None, up_threshold, self._streak_up, reason="scrape_failed")

        if concurrency > up_threshold:
            self._streak_up += 1
        else:
            self._streak_up = 0

        down_threshold: float | None = None
        if cfg.concurrency_factor_down is not None:
            down_threshold = cfg.concurrency_factor_down * cfg.expected_workers
            if concurrency < down_threshold:
                self._streak_down += 1
            else:
                self._streak_down = 0

        if self._campaign_active:
            return Decision("noop", concurrency, up_threshold, self._streak_up, reason="campaign_active")

        if self._in_cooldown():
            return Decision("noop", concurrency, up_threshold, self._streak_up, reason="cooldown")

        if self._streak_up >= cfg.stable_polls:
            self._streak_up = 0
            self._streak_down = 0
            decision = Decision(
                "switch_up", concurrency, up_threshold, cfg.stable_polls, target=cfg.strategy_up,
                reason=f"concurrency {concurrency} > {up_threshold:.0f} for {cfg.stable_polls} polls",
            )
            logger.info("[TP/PP] SWITCH UP -> %s: %s", cfg.strategy_up, decision.reason)
            return decision

        if (
            cfg.strategy_down is not None
            and down_threshold is not None
            and self._streak_down >= cfg.stable_polls
        ):
            self._streak_up = 0
            self._streak_down = 0
            decision = Decision(
                "switch_down", concurrency, up_threshold, cfg.stable_polls, target=cfg.strategy_down,
                reason=(
                    f"concurrency {concurrency} < {down_threshold:.0f} for "
                    f"{cfg.stable_polls} polls"
                ),
            )
            logger.info("[TP/PP] SWITCH DOWN -> %s: %s", cfg.strategy_down, decision.reason)
            return decision

        logger.debug(
            "[TP/PP] poll: concurrency=%d up_threshold=%.0f streak_up=%d streak_down=%d",
            concurrency,
            up_threshold,
            self._streak_up,
            self._streak_down,
        )
        return Decision("noop", concurrency, up_threshold, self._streak_up, reason="below_watermark")

    def _in_cooldown(self) -> bool:
        if self._last_campaign_end is None:
            return False
        return (time.monotonic() - self._last_campaign_end) < self.cfg.cooldown_s

    # -- campaign -------------------------------------------------------
    async def _run_campaign(self, target: Strategy) -> CampaignResult:
        """Switch every configured worker to ``target``, sequentially.

        Sequential on purpose: each switch is a collective across that worker's
        ranks, so two concurrent switches would each hold GPUs while the other
        drains.  It also makes "never remove the last routable worker" trivially
        checkable when ``pause_routing`` is on.
        """
        started = time.monotonic()
        self._campaign_active = True
        outcomes: list[WorkerOutcome] = []
        switched = skipped = 0
        aborted: str | None = None
        try:
            for url in self.cfg.worker_urls:
                outcome = await self._switch_one(url, target)
                outcomes.append(outcome)
                if outcome.abort:
                    aborted = f"{url}: {outcome.reason}"
                    logger.error("[TP/PP] campaign ABORTED at %s: %s", url, outcome.reason)
                    break
                if outcome.action == "switched":
                    switched += 1
                else:
                    skipped += 1
        finally:
            self._campaign_active = False
            self._last_campaign_end = time.monotonic()
            self._campaigns_run += 1

        elapsed = time.monotonic() - started
        result = CampaignResult(switched, skipped, aborted, elapsed, tuple(outcomes))
        logger.info(
            "[TP/PP] campaign #%d to %s finished: switched=%d skipped=%d aborted=%s elapsed=%.1fs",
            self._campaigns_run,
            target,
            switched,
            skipped,
            aborted or "no",
            elapsed,
        )
        return result

    async def _switch_one(self, url: str, target: Strategy) -> WorkerOutcome:
        state = await self._read_state(url)
        if state is None:
            # Unreachable is not fatal: the fleet keeps serving, and a worker
            # that is restarting will be picked up by a later campaign.
            return WorkerOutcome(url, "skipped", "unreachable")

        status = state.get("status")
        if status == "unsupported":
            return WorkerOutcome(url, "skipped", "native vLLM, no elastic switching")
        if status != "ok":
            return WorkerOutcome(url, "skipped", f"state status={status!r}")

        if state.get("failed"):
            # The fork is explicit that a failed switch is unrecoverable and the
            # engine must be restarted.  Continuing would cascade.
            return WorkerOutcome(url, "aborted", "worker reports a failed switch (needs restart)", abort=True)

        if state.get("is_switching"):
            return WorkerOutcome(url, "skipped", "switch already in progress (manual curl?)")

        current = (state.get("tensor_parallel_size"), state.get("pipeline_parallel_size"))
        if current == target.as_tuple():
            return WorkerOutcome(url, "skipped", f"already at {target}")

        physical = state.get("physical_world_size")
        if isinstance(physical, int) and target.world_size > physical:
            # Hard fork constraint: the target must fit the world prestarted at
            # launch.  Refuse before touching the engine.
            return WorkerOutcome(
                url,
                "aborted",
                f"target {target} (world {target.world_size}) exceeds physical_world_size {physical}",
                abort=True,
            )

        payload: dict[str, Any] = {
            "new_world_size": target.world_size,
            "target_tensor_parallel_size": target.tp,
            "target_pipeline_parallel_size": target.pp,
            "request_handling": "wait",
            "admission_handling": "queue",
            "retry_after": 1,
            "pause_routing": self.cfg.pause_routing,
        }
        logger.info(
            "[TP/PP] switching %s from tp=%s pp=%s to %s (pause_routing=%s)",
            url,
            current[0],
            current[1],
            target,
            self.cfg.pause_routing,
        )
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._post_json,
                    url + CONTROL_SWITCH_PATH,
                    payload,
                    # Inner socket timeout sits just outside the outer bound so
                    # wait_for is what fires, giving a single clean TimeoutError.
                    self.cfg.switch_timeout_s + 5.0,
                ),
                timeout=self.cfg.switch_timeout_s,
            )
        except asyncio.TimeoutError:
            # A timeout does NOT mean the switch failed -- the worker may still
            # be mid-drain, and the thread we abandoned can still complete.  The
            # next campaign's state read is what establishes the truth.
            return WorkerOutcome(
                url,
                "aborted",
                f"switch did not report within {self.cfg.switch_timeout_s:.0f}s "
                "(worker may still be switching; do not assume failure)",
                abort=True,
                elapsed_s=time.monotonic() - started,
            )
        except Exception as exc:
            return WorkerOutcome(
                url, "aborted", f"switch request failed: {exc}", abort=True,
                elapsed_s=time.monotonic() - started,
            )

        elapsed = time.monotonic() - started
        resp_status = response.get("status")
        message = response.get("message", "")

        if resp_status == "ok":
            # Belt-and-braces: the response already means "complete", so this GET
            # only exists to catch a regression in the worker-side state fix.
            after = await self._read_state(url)
            observed = (
                (after or {}).get("tensor_parallel_size"),
                (after or {}).get("pipeline_parallel_size"),
            )
            if after is not None and observed != target.as_tuple():
                logger.error(
                    "[TP/PP] %s reported ok but state says tp=%s pp=%s (expected %s); "
                    "the worker's reported strategy is stale -- see the "
                    "_applied_parallel_strategy fix",
                    url,
                    observed[0],
                    observed[1],
                    target,
                )
                return WorkerOutcome(
                    url, "switched",
                    f"ok but state still reports tp={observed[0]} pp={observed[1]} (stale)",
                    elapsed_s=elapsed,
                )
            logger.info("[TP/PP] %s now at %s in %.1fs", url, target, elapsed)
            return WorkerOutcome(url, "switched", f"now at {target}", elapsed_s=elapsed)

        if resp_status == "conflict":
            # Another reconfig holds the worker's _engine_reconfig_lock (sleep,
            # wake, elastic-EP scale, or a concurrent manual switch).  The engine
            # is fine; just not ours to move right now.
            return WorkerOutcome(url, "skipped", f"conflict: {message}", elapsed_s=elapsed)

        if resp_status == "unavailable":
            # The worker is shutting itself down after an unsafe switch.
            return WorkerOutcome(
                url, "aborted", f"worker unavailable, restarting: {message}", abort=True,
                elapsed_s=elapsed,
            )

        return WorkerOutcome(
            url, "aborted", f"status={resp_status!r}: {message}", abort=True, elapsed_s=elapsed
        )

    async def _read_state(self, url: str) -> dict[str, Any] | None:
        try:
            return await asyncio.to_thread(
                self._get_json, url + CONTROL_STATE_PATH, self.cfg.http_timeout_s
            )
        except Exception as exc:
            logger.warning("[TP/PP] state read from %s failed: %s", url, exc)
            return None

    # -- introspection (tests / logs) -----------------------------------
    @property
    def campaign_active(self) -> bool:
        return self._campaign_active

    @property
    def campaigns_run(self) -> int:
        return self._campaigns_run

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.enabled,
            "campaign_active": self._campaign_active,
            "campaigns_run": self._campaigns_run,
            "streak_up": self._streak_up,
            "streak_down": self._streak_down,
            "in_cooldown": self._in_cooldown(),
            "expected_workers": self.cfg.expected_workers,
            "strategy_up": str(self.cfg.strategy_up),
            "strategy_down": str(self.cfg.strategy_down) if self.cfg.strategy_down else None,
        }


# ---------------------------------------------------------------------------
# Manual entry point
# ---------------------------------------------------------------------------
def main() -> int:
    """Run the controller standalone from env config.

    Convenience for manual testing only -- it lets one campaign be driven against
    a running worker without restarting the frontend.  The deployment path is the
    in-frontend asyncio task spawned from ``frontend/main.py``.
    """
    logging.basicConfig(
        level=os.environ.get("DYN_ELASTIC_SWITCH_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        cfg = ControllerConfig.from_env()
    except ValueError as exc:
        logger.error("[TP/PP] invalid configuration: %s", exc)
        return 2
    if not cfg.enabled:
        logger.error("[TP/PP] %sENABLE is not set; refusing to run", ENV_PREFIX)
        return 2
    controller = ElasticSwitchController(cfg)
    try:
        asyncio.run(controller.run())
    except KeyboardInterrupt:
        logger.info("[TP/PP] interrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
