"""Lightweight generation observability: trace ids, structured logs, per-call metrics and evals.

A single run lives in a contextvar so every provider call made inside that asyncio task is
collected without threading parameters through the call stack. Safe to call from anywhere: when
no run is active (unit tests, ad-hoc calls) every function is a no-op.
"""
import contextvars
import json
import logging
import time
from uuid import uuid4

_log = logging.getLogger("genobs")
_RUN: contextvars.ContextVar | None = contextvars.ContextVar("gen_run", default=None)


def new_trace() -> str:
    return uuid4().hex[:16]


def start_run(pillar: str, date: str, *, trace_id: str | None = None, trigger: str = "auto",
              count: int = 1, requested_by: str = "auto") -> str:
    trace_id = trace_id or new_trace()
    _RUN.set({"trace_id": trace_id, "pillar": pillar, "date": date, "trigger": trigger,
              "count": count, "requested_by": requested_by, "t0": time.monotonic(),
              "calls": [], "tokens": {"in": 0, "out": 0}, "events": []})
    log("generation.start", trace=trace_id, pillar=pillar, date=date, trigger=trigger,
        count=count, requested_by=requested_by)
    return trace_id


def current() -> dict | None:
    return _RUN.get()


def record_call(provider: str, model: str, latency_ms: float, ok: bool, *,
                error: str | None = None, attempt: int = 0) -> None:
    run = _RUN.get()
    if run is None:
        return
    run["calls"].append({"provider": provider, "model": model, "ms": round(latency_ms, 1),
                         "ok": ok, "kind": _kind(error) if error else None, "attempt": attempt})


def record_tokens(tokens_in: int | None, tokens_out: int | None) -> None:
    run = _RUN.get()
    if run is None:
        return
    run["tokens"]["in"] += int(tokens_in or 0)
    run["tokens"]["out"] += int(tokens_out or 0)


def record_event(kind: str, **fields) -> None:
    run = _RUN.get()
    if run is None:
        return
    run["events"].append({"kind": kind, **fields})


def _kind(err) -> str:
    s = (type(err).__name__ if not isinstance(err, str) else err) + " " + str(err)
    low = s.lower()
    if "timeout" in low:
        return "timeout"
    if "quota" in low or "429" in low or "rate limit" in low:
        return "quota"
    if "json_validate" in low or "failed_generation" in low or "truncat" in low or "generation" in low:
        return "truncated"
    if "block" in low or "401" in low or "403" in low or "404" in low:
        return "blocked"
    if "unavailable" in low:
        return "unavailable"
    return "error"


def snapshot() -> dict:
    run = _RUN.get()
    if run is None:
        return {}
    calls = run["calls"]
    by_provider: dict[str, dict] = {}
    for c in calls:
        b = by_provider.setdefault(c["provider"], {"calls": 0, "fail": 0, "ms": 0})
        b["calls"] += 1
        b["ms"] += c["ms"]
        if not c["ok"]:
            b["fail"] += 1
    gemini_ok = any(c["provider"] == "gemini" and c["ok"] for c in calls)
    groq_ok = any(c["provider"] == "groq" and c["ok"] for c in calls)
    kinds = {}
    for c in calls:
        if not c["ok"] and c["kind"]:
            kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    return {
        "trace_id": run["trace_id"],
        "calls": len(calls),
        "ok_calls": sum(1 for c in calls if c["ok"]),
        "fail_calls": sum(1 for c in calls if not c["ok"]),
        "retries": sum(1 for c in calls if c["attempt"] > 0),
        "used_fallback": bool(groq_ok and not gemini_ok),
        "tokens": dict(run["tokens"]),
        "by_provider": {p: {"calls": v["calls"], "fail": v["fail"],
                            "avg_ms": round(v["ms"] / v["calls"], 1) if v["calls"] else 0}
                        for p, v in by_provider.items()},
        "error_kinds": kinds,
        "events": run["events"][-20:],
        "wall_ms": round((time.monotonic() - run["t0"]) * 1000, 1),
    }


def finish_run(status: str) -> dict:
    snap = snapshot()
    run = _RUN.get()
    if run is not None:
        log("generation.finish", trace=run["trace_id"], pillar=run["pillar"], date=run["date"],
            status=status, calls=snap.get("calls", 0), fail=snap.get("fail_calls", 0),
            used_fallback=snap.get("used_fallback"), wall_ms=snap.get("wall_ms"),
            tokens=snap.get("tokens"), error_kinds=snap.get("error_kinds"))
    _RUN.set(None)
    return snap


def log(event: str, **fields) -> None:
    try:
        _log.info(json.dumps({"ts": time.time(), "event": event, **fields}, default=str))
    except Exception:  # noqa: BLE001  logging must never break generation
        pass
