from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from solarapp.core import events, export
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry

app = FastAPI(title="SolarQuick")
STATIC = Path(__file__).parent / "static"


class Query(BaseModel):
    q: str


class ExportRequest(BaseModel):
    spec: dict          # the block the browser is already displaying
    format: str = "csv"  # "csv" | "json"
    query: str | None = None


@app.get("/")
def index() -> FileResponse:
    # no-store: the UI and the API evolve together, so a cached index.html from
    # a previous version would drive a newer API. Costs one small local request.
    return FileResponse(STATIC / "index.html",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/sources")
def sources() -> list[dict]:
    return [
        {"name": s.name, "display_name": s.display_name, "keywords": s.keywords}
        for s in get_registry().sources.values()
    ]


@app.post("/api/query")
def query(body: Query) -> JSONResponse:
    reg = get_registry()
    t_start = time.perf_counter()
    timings: dict[str, int] = {}

    parsed = parse(body.q, reg)
    timings["parse"] = round((time.perf_counter() - t_start) * 1000)
    if not parsed.ok:
        return JSONResponse({"errors": parsed.errors}, status_code=422)

    t0 = time.perf_counter()
    try:  # event queries ("biggest flare since 2014") resolve against HEK here
        events.expand(parsed)
    except RuntimeError as e:
        return JSONResponse({"errors": [str(e)]}, status_code=422)
    elapsed = round((time.perf_counter() - t0) * 1000)
    if elapsed:  # only when an event query actually resolved something
        timings["resolve_event"] = elapsed

    def run(req) -> tuple[dict | None, str | None, str, int]:
        src = reg.get(req.source)
        t = time.perf_counter()
        try:
            spec = src.fetch(parsed.start, parsed.end, **req.extras)
            err = None
        except Exception as e:  # surfacing fetch problems to the UI beats a 500
            spec, err = None, f"{src.display_name}: {e}"
        return spec, err, req.source, round((time.perf_counter() - t) * 1000)

    # sources are network-bound and independent — fetch them concurrently
    with ThreadPoolExecutor(max_workers=min(4, len(parsed.requests))) as pool:
        outcomes = list(pool.map(run, parsed.requests))
    specs = [s for s, _, _, _ in outcomes if s is not None]
    errors = [e for _, e, _, _ in outcomes if e is not None]
    for _, _, name, ms in outcomes:  # same source twice (aia 171 and 304) → keep the slower
        timings[name] = max(ms, timings.get(name, 0))

    if not specs:
        return JSONResponse({"errors": errors}, status_code=502)

    if len(parsed.requests) == 1:
        spec = specs[0]
    else:
        spec = {"kind": "multi", "items": specs}
        if errors:
            spec["note"] = "Some sources failed — " + " · ".join(errors)

    if parsed.correction_note:  # tell the user what we read, never silently guess
        spec["note"] = " ".join(filter(None, [parsed.correction_note, spec.get("note")]))

    # wall clock is less than the sum when sources run concurrently — that gap
    # is exactly what the concurrency is buying, so report both
    timings["total"] = round((time.perf_counter() - t_start) * 1000)
    spec["timings"] = timings

    spec["parsed"] = {
        "source": parsed.source,
        "sources": [r.source for r in parsed.requests],
        "start": parsed.start.isoformat(),
        "end": parsed.end.isoformat(),
        "corrections": parsed.corrections,
        "rewritten": parsed.rewritten,
    }
    return JSONResponse(spec)


@app.post("/api/export")
def export_data(body: ExportRequest) -> Response:
    """Convert a spec the browser already holds into a download.

    Deliberately takes the spec rather than re-running the query: the file is
    then exactly the numbers on screen, and the archives aren't fetched twice.
    """
    fmt = body.format.lower()
    if fmt not in ("csv", "json"):
        return JSONResponse({"errors": [f"Unsupported export format: {fmt}"]},
                            status_code=400)
    try:
        if fmt == "json":
            import json
            payload = json.dumps(
                {"query": body.query, **body.spec}, indent=2, allow_nan=False,
            )
            media = "application/json"
        else:
            payload = export.spec_to_csv(body.spec, body.query)
            media = "text/csv"
    except export.NotExportable as e:
        return JSONResponse({"errors": [str(e)]}, status_code=422)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
        # the spec is client-supplied, so a structural mismatch is a bad
        # request, not a server fault — never let it surface as a 500
        return JSONResponse(
            {"errors": [f"Could not export this result: {type(e).__name__}: {e}"]},
            status_code=422,
        )

    name = export.filename(body.spec, fmt)
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
