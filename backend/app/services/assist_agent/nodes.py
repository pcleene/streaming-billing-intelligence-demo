"""Async node implementations for the AI-assist agent graph.

Each node is `async def fn(state, *, deps) -> dict` returning a partial
state update. `deps` is a small `AssistAgentDeps` dataclass injected by
the graph builder (closure-capture) so nodes are testable without
LangGraph running — call them directly with `AsyncMock`s in tests.

Per-node contract:
  * record `started_at` / `finished_at` / `duration_ms` into a trace
    entry,
  * catch exceptions, push to `errors`, push a trace entry with
    `error` field set,
  * keep going on non-fatal failures so the synthesizer can degrade
    gracefully rather than the worker losing the whole assist payload.

`load_case` is the only node where a failure is fatal (no case → no
synthesis). The graph routes around it by checking `state.get('case')`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Protocol

from .state import AgentTraceEntry, AssistAgentState


# --- Protocols -------------------------------------------------------


class AnalyticsTools(Protocol):
    """Three async analytics methods the deep path runs concurrently.

    F3 will land the concrete implementation; the graph only depends
    on this Protocol so this PR's tests can use plain `AsyncMock`s.
    """

    async def customer_pattern_30d(self, customer_id: str) -> dict: ...

    async def rule_type_frequency(self, rule_type: str, days: int = 7) -> dict: ...

    async def drift_snapshot(self, feature_names: list[str]) -> dict: ...


# --- Deps -------------------------------------------------------------


@dataclass(slots=True)
class AssistAgentDeps:
    """Bundle of pluggable dependencies a node may need.

    Production wiring builds this with real repos + Anthropic-backed
    classifier/synthesizer. Tests build it with `AsyncMock` doubles +
    deterministic lambda classifier/synthesizer.
    """

    case_repo: Any  # QuarantineCaseRepository or duck-type with `get_by_id`
    classify_fn: Callable[..., Awaitable[dict]]
    analytics_tools: AnalyticsTools
    vector_search_fn: Callable[..., Awaitable[list[dict]]]
    synthesize_fn: Callable[..., Awaitable[dict]]
    persist_fn: Callable[..., Awaitable[None]]
    # Live-state hydration deps (used by the `load_live_state` node).
    # Optional: when None, the node degrades — the corresponding state
    # key just stays unset and downstream code keeps using the case
    # snapshot. This keeps existing tests / wiring forward-compatible.
    customer_360_service: Any = None  # has `.get_profile(cust_id, rich=True)`
    transaction_repo: Any = None  # has `.get_by_id(txn_id)`


# --- Trace helpers ----------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _trace_entry(
    node: str,
    started_at: datetime,
    *,
    output_keys: list[str] | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Build a serialised `AgentTraceEntry` dict.

    Centralised so every node (and tests) emits the same shape; the
    `finished_at` / `duration_ms` are computed at call time so the
    helper can also be used for synthetic entries.
    """
    finished = _utcnow()
    duration_ms = max(0.0, (finished - started_at).total_seconds() * 1000.0)
    entry = AgentTraceEntry(
        node=node,
        started_at=started_at,
        finished_at=finished,
        duration_ms=duration_ms,
        output_keys=list(output_keys or []),
        error=error,
        metadata=dict(metadata or {}),
    )
    return entry.model_dump(mode="json")


async def _maybe_await(value: Any) -> Any:
    """Tolerate sync-or-async callables.

    Tests often pass plain lambdas; production wires async LLM calls.
    Rather than make every node test set up `AsyncMock`, accept either.
    """
    if asyncio.iscoroutine(value):
        return await value
    return value


# --- Nodes ------------------------------------------------------------


async def load_case(state: AssistAgentState, *, deps: AssistAgentDeps) -> dict:
    """Fetch the case doc by id.

    Failure here IS fatal (no downstream node can run without a case).
    Rather than raising — which would short-circuit the whole graph
    via LangGraph's exception path — we record the error in `errors`
    and leave `case` unset so the conditional edge can route to END.
    """
    started = _utcnow()
    case_id = state.get("case_id")
    try:
        if not case_id:
            raise ValueError("load_case: state['case_id'] is required")
        case = await deps.case_repo.get_by_id(case_id)
        if case is None:
            err = f"case_not_found: {case_id}"
            return {
                "trace": [_trace_entry("load_case", started, error=err)],
                "errors": [f"load_case: {err}"],
            }
        return {
            "case": case,
            "trace": [_trace_entry("load_case", started, output_keys=["case"])],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "trace": [
                _trace_entry(
                    "load_case", started, error=f"{type(exc).__name__}: {exc}"
                )
            ],
            "errors": [f"load_case: {exc}"],
        }


async def load_live_state(
    state: AssistAgentState, *, deps: AssistAgentDeps
) -> dict:
    """Hydrate the live `customers_residential` + V3 `transactions` docs.

    The case snapshot (`customer_snapshot`, `transaction_summary`) is a
    point-in-time projection captured when the case was opened. The
    rich live docs carry data that snapshot drops — entitlements,
    interaction history, full per-line items, discount structure,
    cross-entity metrics. The synthesizer + prompt builder consume
    these to ground the assist payload in the freshest state.

    Both fetches run concurrently via `asyncio.gather(...,
    return_exceptions=True)` so a slow / missing transaction never
    blocks the customer fetch and vice versa. Failure of either is
    non-fatal — the state key is left unset and the trace records
    the error so the analyst can see why the live-state arm degraded.

    No-op (with a single trace entry tagged `skipped`) when neither
    dep is wired — keeps the node cheap on test setups that haven't
    plumbed through the new deps yet.
    """
    started = _utcnow()
    case = state.get("case") or {}
    customer_id = case.get("customer_id") or ""
    txn_summary = case.get("transaction_summary") or {}
    transaction_id = (
        txn_summary.get("transaction_id")
        or case.get("transaction_id")
        or ""
    )

    cust_svc = deps.customer_360_service
    txn_repo = deps.transaction_repo
    if cust_svc is None and txn_repo is None:
        return {
            "trace": [
                _trace_entry(
                    "load_live_state",
                    started,
                    metadata={"skipped": "deps_not_wired"},
                )
            ],
        }

    async def _fetch_customer() -> Any:
        if cust_svc is None or not customer_id:
            return None
        return await _maybe_await(
            cust_svc.get_profile(customer_id, rich=True)
        )

    async def _fetch_transaction() -> Any:
        if txn_repo is None or not transaction_id:
            return None
        return await _maybe_await(txn_repo.get_by_id(transaction_id))

    cust_res, txn_res = await asyncio.gather(
        _fetch_customer(), _fetch_transaction(), return_exceptions=True
    )

    delta: dict = {}
    output_keys: list[str] = []
    errors: list[str] = []

    if isinstance(cust_res, Exception):
        errors.append(
            f"customer: {type(cust_res).__name__}: {cust_res}"
        )
    elif isinstance(cust_res, dict) and cust_res:
        delta["customer"] = cust_res
        output_keys.append("customer")

    if isinstance(txn_res, Exception):
        errors.append(
            f"transaction: {type(txn_res).__name__}: {txn_res}"
        )
    elif isinstance(txn_res, dict) and txn_res:
        delta["transaction"] = txn_res
        output_keys.append("transaction")

    delta["trace"] = [
        _trace_entry(
            "load_live_state",
            started,
            output_keys=output_keys,
            error=("; ".join(errors) or None),
            metadata={
                "customer_hydrated": "customer" in delta,
                "transaction_hydrated": "transaction" in delta,
            },
        )
    ]
    if errors:
        delta["errors"] = [f"load_live_state: {e}" for e in errors]
    return delta


async def classify(state: AssistAgentState, *, deps: AssistAgentDeps) -> dict:
    """Run the case classifier.

    Tolerates sync-or-async classifiers. Swallows exceptions so a
    classifier failure (LLM timeout, JSON parse error) doesn't abort
    the whole graph — the router defaults to "deep" when classification
    is missing.
    """
    started = _utcnow()
    try:
        out = await _maybe_await(deps.classify_fn(state.get("case") or {}))
        classification = dict(out or {})
        return {
            "classification": classification,
            "trace": [
                _trace_entry(
                    "classify",
                    started,
                    output_keys=["classification"],
                    metadata={
                        "recommended_path": classification.get("recommended_path"),
                    },
                )
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "trace": [
                _trace_entry(
                    "classify", started, error=f"{type(exc).__name__}: {exc}"
                )
            ],
            "errors": [f"classify: {exc}"],
        }


async def analytics(state: AssistAgentState, *, deps: AssistAgentDeps) -> dict:
    """Run the three analytics tools concurrently.

    Independent tools → `asyncio.gather(..., return_exceptions=True)`
    so one slow / failing tool doesn't bring down the whole node. Each
    failure is recorded into both the per-bucket result (as `{"error":
    ...}`) and the trace entry's metadata.
    """
    started = _utcnow()
    case = state.get("case") or {}
    customer_id = case.get("customer_id") or ""
    rules = case.get("rules_triggered") or []
    rule_type = ""
    for r in rules:
        if isinstance(r, dict) and r.get("rule_type"):
            rule_type = r["rule_type"]
            break
    feature_names: list[str] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        ev = r.get("evidence") or {}
        fn = ev.get("feature_name")
        if isinstance(fn, str) and fn and fn not in feature_names:
            feature_names.append(fn)

    tools = deps.analytics_tools
    coros = [
        tools.customer_pattern_30d(customer_id),
        tools.rule_type_frequency(rule_type) if rule_type else _noop_skip("no_rule_type"),
        tools.drift_snapshot(feature_names),
    ]

    results = await asyncio.gather(*coros, return_exceptions=True)
    per_tool_errors: list[str] = []

    def _norm(name: str, r: Any) -> dict:
        if isinstance(r, Exception):
            err = f"{type(r).__name__}: {r}"
            per_tool_errors.append(f"{name}: {err}")
            return {"error": err}
        return dict(r) if isinstance(r, dict) else {"value": r}

    pattern = _norm("customer_pattern", results[0])
    rule_freq = _norm("rule_type_frequency", results[1])
    drift = _norm("drift_snapshot", results[2])

    delta: dict = {
        "analytics": {
            "customer_pattern": pattern,
            "rule_type_frequency": rule_freq,
            "drift_snapshot": drift,
        },
        "trace": [
            _trace_entry(
                "analytics",
                started,
                output_keys=["analytics"],
                error=("; ".join(per_tool_errors) or None),
                metadata={"partial_failures": per_tool_errors},
            )
        ],
    }
    if per_tool_errors:
        delta["errors"] = [f"analytics: {e}" for e in per_tool_errors]
    return delta


async def _noop_skip(reason: str) -> dict:
    return {"skipped": reason}


async def vector_search(
    state: AssistAgentState, *, deps: AssistAgentDeps
) -> dict:
    """Call the existing history-corpus vector search.

    Failure isn't fatal — the synthesizer can compose without prior
    cases (it just won't have grounding citations).
    """
    started = _utcnow()
    try:
        hits = await _maybe_await(deps.vector_search_fn(state.get("case") or {}))
        retrieval = list(hits or [])
        return {
            "retrieval": retrieval,
            "trace": [
                _trace_entry(
                    "vector_search",
                    started,
                    output_keys=["retrieval"],
                    metadata={"hit_count": len(retrieval)},
                )
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "retrieval": [],
            "trace": [
                _trace_entry(
                    "vector_search",
                    started,
                    error=f"{type(exc).__name__}: {exc}",
                )
            ],
            "errors": [f"vector_search: {exc}"],
        }


async def synthesize(state: AssistAgentState, *, deps: AssistAgentDeps) -> dict:
    """Compose the final AiAssist payload from everything in state.

    A synthesis failure DOES propagate via `errors` but we still return
    an empty dict for `synthesis` so `persist` has something to write
    (the worker decides whether to keep / drop a degraded payload).
    """
    started = _utcnow()
    try:
        out = await _maybe_await(deps.synthesize_fn(state))
        synthesis = dict(out or {})
        return {
            "synthesis": synthesis,
            "trace": [
                _trace_entry(
                    "synthesize", started, output_keys=["synthesis"]
                )
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "synthesis": {},
            "trace": [
                _trace_entry(
                    "synthesize",
                    started,
                    error=f"{type(exc).__name__}: {exc}",
                )
            ],
            "errors": [f"synthesize: {exc}"],
        }


async def persist(state: AssistAgentState, *, deps: AssistAgentDeps) -> dict:
    """Hand the final synthesis + trace off to the persist function.

    The persist callable is supplied by the worker (F2) so this PR
    doesn't have to know about Mongo update semantics — keeps the
    graph re-runnable and pure-ish.
    """
    started = _utcnow()
    try:
        case_id = state.get("case_id") or ""
        synthesis = state.get("synthesis") or {}
        trace_so_far = list(state.get("trace") or [])
        await _maybe_await(deps.persist_fn(case_id, synthesis, trace_so_far))
        return {
            "trace": [_trace_entry("persist", started)],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "trace": [
                _trace_entry(
                    "persist", started, error=f"{type(exc).__name__}: {exc}"
                )
            ],
            "errors": [f"persist: {exc}"],
        }
