"""One query, start to finish, as a stream of progress events.

The work was always the same — parse, expand events, fetch every source
concurrently, assemble one spec — but it used to happen behind a single POST
that said nothing until it was done. A query that takes twelve seconds because
NCEI is slow looked exactly like a query that had hung.

So the pipeline is a generator. `/api/query` drains it and returns the last
event; `/api/query/stream` forwards each event to the browser as NDJSON, which
is what makes the loading display real: it names what was understood, lists the
sources being fetched with how long each usually takes, and ticks them off as
they land. Both endpoints run exactly the same code, so the streaming view can
never disagree with the plain one.

Events (each a JSON object with an `event` key):
  parsing      — the query is being read
  parsed       — {sources: [{name, display, estimate_ms, confident, cached}],
                  start, end, note}
  source_done  — {source, ms, ok, error, cached}
  result       — {spec}   the finished PlotSpec, exactly as /api/query returns
  error        — {errors, status}
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Iterator

from . import events, resultcache, timings, workspace
from .parser import ParsedQuery, SourceRequest, parse
from .registry import Registry

MAX_WORKERS = 4


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def run(query: str, registry: Registry) -> Iterator[dict]:
    """Run `query`, yielding progress events and finally the result."""
    t_start = time.perf_counter()
    stage: dict[str, int] = {}

    yield {"event": "parsing", "query": query}

    parsed = parse(query, registry)
    stage["parse"] = round((time.perf_counter() - t_start) * 1000)
    if not parsed.ok:
        yield {"event": "error", "errors": parsed.errors, "status": 422}
        return

    t0 = time.perf_counter()
    try:  # event queries ("biggest flare since 2014") resolve against HEK here
        events.expand(parsed)
    except RuntimeError as e:
        yield {"event": "error", "errors": [str(e)], "status": 422}
        return
    elapsed = round((time.perf_counter() - t0) * 1000)
    if elapsed:  # only when an event query actually resolved something
        stage["resolve_event"] = elapsed
    yield from _execute(parsed, registry, t_start, stage)


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def run_structured(body: dict, registry: Registry) -> Iterator[dict]:
    """The form's request (see core/workspace.py): same events as `run`.

    body: {start, end, requests: [{source, extras}], frames?, crop_fov?, label?}
    """
    t_start = time.perf_counter()
    stage: dict[str, int] = {}
    yield {"event": "parsing", "query": body.get("label") or "selection"}
    try:
        start, end = _parse_time(body["start"]), _parse_time(body["end"])
    except (KeyError, ValueError, AttributeError):
        yield {"event": "error", "errors": ["Start and end must be ISO date-times."],
               "status": 422}
        return
    if end <= start:
        yield {"event": "error", "errors": ["The end time must be after the start."],
               "status": 422}
        return

    parsed = ParsedQuery(start=start, end=end)
    for r in body.get("requests") or []:
        if registry.get(r.get("source")) is None:
            yield {"event": "error", "errors": [f"Unknown source: {r.get('source')}"],
                   "status": 422}
            return
        parsed.requests.append(SourceRequest(r["source"], dict(r.get("extras") or {})))
    notes = []
    if not parsed.requests:
        parsed.requests = workspace.default_requests(registry, start, end)
        if not parsed.requests:
            yield {"event": "error", "status": 422,
                   "errors": ["No default sources were observing in this window."]}
            return

    t0 = time.perf_counter()
    note = workspace.apply_image_options(
        parsed.requests, registry, start, end,
        frames=body.get("frames"), crop_fov=body.get("crop_fov"))
    elapsed = round((time.perf_counter() - t0) * 1000)
    if elapsed > 5:
        stage["resolve_event"] = elapsed
    if note:
        notes.append(note)
    parsed.source, parsed.extras = parsed.requests[0].source, parsed.requests[0].extras
    yield from _execute(parsed, registry, t_start, stage, " ".join(notes) or None)


def _execute(parsed: ParsedQuery, registry: Registry, t_start: float,
             stage: dict[str, int], extra_note: str | None = None) -> Iterator[dict]:
    """Fetch every request of an already-resolved query, streaming progress."""
    cache = resultcache.get_cache()
    use_cache = resultcache.enabled()
    learned = timings.get_timings()
    keys = [resultcache.key_for(r.source, r.extras, parsed.start, parsed.end)
            for r in parsed.requests]
    # a cache hit returns instantly, so it is announced as such rather than
    # given an estimate the user would watch expire in one frame
    will_hit = [use_cache and cache.peek(k) for k in keys]

    plan = []
    for req, hit in zip(parsed.requests, will_hit):
        src = registry.get(req.source)
        est = None if hit else learned.estimate(req.source)
        plan.append({
            "name": req.source,
            "display": src.display_name,
            "options": _describe(src, req.extras),
            "estimate_ms": est[0] if est else None,
            "confident": bool(est and est[1]),
            "cached": bool(hit),
        })
    yield {
        "event": "parsed",
        "sources": plan,
        "parallel": MAX_WORKERS,
        "start": _iso(parsed.start),
        "end": _iso(parsed.end),
        "note": " ".join(filter(None, [parsed.correction_note, extra_note])) or None,
    }

    def work(index: int) -> dict:
        req, key = parsed.requests[index], keys[index]
        src = registry.get(req.source)
        t = time.perf_counter()
        if use_cache:
            hit = cache.get(key)
            if hit is not None:
                return {"index": index, "source": req.source, "spec": hit, "error": None,
                        "ms": round((time.perf_counter() - t) * 1000), "cached": True}
        try:
            spec, err = src.fetch(parsed.start, parsed.end, **req.extras), None
            if use_cache:  # only successes: a 503 must not become a cached outage
                cache.put(key, spec, parsed.end)
        except Exception as e:  # surfacing fetch problems to the UI beats a 500
            spec, err = None, f"{src.display_name}: {e}"
        ms = round((time.perf_counter() - t) * 1000)
        if err is None:
            learned.record(req.source, ms)
        return {"index": index, "source": req.source, "spec": spec, "error": err,
                "ms": ms, "cached": False}

    # sources are network-bound and independent — fetch them concurrently, and
    # report each one the moment it lands rather than when the slowest does
    outcomes: list[dict] = []
    cached: list[str] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(parsed.requests))) as pool:
        futures = [pool.submit(work, i) for i in range(len(parsed.requests))]
        for future in as_completed(futures):
            out = future.result()
            outcomes.append(out)
            if out["cached"]:
                cached.append(out["source"])
            # same source twice (aia 171 and 304) → keep the slower
            stage[out["source"]] = max(out["ms"], stage.get(out["source"], 0))
            yield {"event": "source_done", "source": out["source"], "ms": out["ms"],
                   "ok": out["error"] is None, "error": out["error"],
                   "cached": out["cached"]}

    outcomes.sort(key=lambda o: o["index"])  # restore the order they were asked in
    for o in outcomes:  # which request made each block: the UI re-asks per panel
        if o["spec"] is not None:
            req = parsed.requests[o["index"]]
            o["spec"]["request"] = {"source": req.source, "extras": req.extras}
    specs = [o["spec"] for o in outcomes if o["spec"] is not None]
    errors = [o["error"] for o in outcomes if o["error"] is not None]

    if not specs:
        yield {"event": "error", "errors": errors, "status": 502}
        return

    if len(specs) == 1 and len(parsed.requests) == 1 and not extra_note:
        spec = specs[0]
    else:
        spec = {"kind": "multi", "items": specs}
        if errors:
            spec["note"] = "Some sources failed — " + " · ".join(errors)

    if parsed.correction_note:  # tell the user what we read, never silently guess
        spec["note"] = " ".join(filter(None, [parsed.correction_note, spec.get("note")]))
    if extra_note:
        spec["note"] = " ".join(filter(None, [extra_note, spec.get("note")]))

    # wall clock is less than the sum when sources run concurrently — that gap
    # is exactly what the concurrency is buying, so report both
    stage["total"] = round((time.perf_counter() - t_start) * 1000)
    spec["timings"] = stage
    if cached:  # visible, so a surprising result can be checked against a fresh fetch
        spec["cached"] = sorted(set(cached))

    spec["parsed"] = {
        "source": parsed.source,
        "sources": [r.source for r in parsed.requests],
        "requests": [{"source": r.source, "extras": r.extras} for r in parsed.requests],
        "start": _iso(parsed.start),
        "end": _iso(parsed.end),
        "corrections": parsed.corrections,
        "rewritten": parsed.rewritten,
        "summary": parsed.summary,
    }
    yield {"event": "result", "spec": spec}


def _describe(source, extras: dict) -> str:
    """The chosen options in the source's own words ("AIA 193 Å"), for the UI."""
    labels = []
    for var in source.variables:
        wanted = var.extras_for(source)
        if wanted and all(extras.get(k) == v for k, v in wanted.items()):
            labels.append(var.label)
    for key, value in extras.items():
        if key in ("satellite",):
            labels.append(f"GOES-{value}")
    return " · ".join(dict.fromkeys(labels))


def collect(query: str, registry: Registry) -> tuple[dict | None, list[str], int]:
    """Run to completion and return (spec, errors, status) — the non-streaming path."""
    for ev in run(query, registry):
        if ev["event"] == "error":
            return None, ev["errors"], ev["status"]
        if ev["event"] == "result":
            return ev["spec"], [], 200
    return None, ["The query produced no result."], 500
