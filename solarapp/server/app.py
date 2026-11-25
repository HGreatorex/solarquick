from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import (
    FileResponse, JSONResponse, Response, StreamingResponse,
)
from pydantic import BaseModel

from datetime import datetime, timezone

from solarapp.core import events, export, hek, live, pipeline, resultcache, timings
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry

app = FastAPI(title="SolarQuick")
STATIC = Path(__file__).parent / "static"


class Query(BaseModel):
    q: str


class RunRequest(BaseModel):
    """The two-pane form: an explicit window and the ticked sources."""
    start: str
    end: str
    requests: list[dict] = []   # [{source, extras}]; empty = the default view
    frames: int | None = None   # image timeline length
    crop_fov: float | None = None  # None keep, 0 full disk, >0 arcsec at flare
    label: str | None = None


class FrameRequest(BaseModel):
    source: str
    extras: dict = {}
    time: str


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
    """Everything a plugin declares, so the UI can list it without a code change."""
    return [
        {
            "name": s.name,
            "display_name": s.display_name,
            "keywords": s.keywords,
            "display": s.display,
            "variable_key": s.variable_key,
            "options": [
                {"key": v.key, "label": v.label, "keywords": v.keywords,
                 "group": v.group or s.variable_key, "extras": v.extras_for(s)}
                for v in s.variables if not v.hidden
            ],
            "coverage": list(s.coverage),
            "in_summary": s.summary_rank is not None,
        }
        for s in get_registry().sources.values()
    ]


@app.post("/api/query")
def query(body: Query) -> JSONResponse:
    """Run a query and return the finished spec (no progress reporting)."""
    spec, errors, status = pipeline.collect(body.q, get_registry())
    if spec is None:
        return JSONResponse({"errors": errors}, status_code=status)
    return JSONResponse(spec)


@app.post("/api/query/stream")
def query_stream(body: Query) -> StreamingResponse:
    """The same query as NDJSON progress events, one JSON object per line.

    The browser reads these as they arrive, so a twelve-second fetch shows
    what it is waiting for and how long that source usually takes, instead of
    a spinner that could equally mean "working" or "hung". The final `result`
    event carries exactly what /api/query would have returned.
    """
    def lines():
        for event in pipeline.run(body.q, get_registry()):
            yield json.dumps(event) + "\n"

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        # a proxy that buffered this would defeat the whole point
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/parse")
def parse_only(body: Query) -> JSONResponse:
    """Read a sentence into the form: window + requests, nothing fetched.

    Event queries resolve here ("biggest flare since 2014" becomes that
    flare's window and companions), so the form shows exactly what Load will
    fetch. A bare date returns no requests: the form's default view.
    """
    reg = get_registry()
    parsed = parse(body.q, reg)
    if not parsed.ok:
        return JSONResponse({"errors": parsed.errors}, status_code=422)
    try:
        events.expand(parsed)
    except RuntimeError as e:
        return JSONResponse({"errors": [str(e)]}, status_code=422)
    reqs = [] if parsed.summary else [
        {"source": r.source, "extras": r.extras} for r in parsed.requests]
    crop = next((r["extras"]["fov"] for r in reqs if "fov" in r["extras"]), None)
    return JSONResponse({
        "start": parsed.start.isoformat(), "end": parsed.end.isoformat(),
        "requests": reqs, "crop_fov": crop,
        "note": " ".join(filter(None, [parsed.correction_note])) or None,
    })


@app.post("/api/run/stream")
def run_stream(body: RunRequest) -> StreamingResponse:
    """The form's request as NDJSON progress events (see /api/query/stream)."""
    def lines():
        for event in pipeline.run_structured(body.model_dump(), get_registry()):
            yield json.dumps(event) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@app.get("/api/flare")
def flare(start: str, end: str) -> JSONResponse:
    """The largest catalogued flare in a window — the image grid's peak tick."""
    try:
        f = hek.biggest_flare(_utc(start), _utc(end))
    except ValueError:
        return JSONResponse({"errors": ["start/end must be ISO date-times"]}, status_code=422)
    except Exception as e:
        return JSONResponse({"errors": [f"HEK: {e}"]}, status_code=502)
    if f is None:
        return JSONResponse({"flare": None})
    return JSONResponse({"flare": {
        "class": f.goes_class, "peak": f.peak.isoformat(), "start": f.start.isoformat(),
        "end": f.end.isoformat(), "label": f.label(),
        "x": f.hpc_x if f.has_position else None, "y": f.hpc_y if f.has_position else None,
    }})


@app.post("/api/frame")
def frame(body: FrameRequest) -> JSONResponse:
    """One image frame nearest a time, for a panel already on screen."""
    src = get_registry().get(body.source)
    if src is None or not hasattr(src, "frame_at"):
        return JSONResponse({"errors": [f"{body.source} cannot fetch single frames"]},
                            status_code=422)
    try:
        return JSONResponse(src.frame_at(_utc(body.time), **body.extras))
    except (ValueError, KeyError) as e:
        return JSONResponse({"errors": [f"Bad frame request: {e}"]}, status_code=422)


@app.get("/api/live")
def live_view(panels: str = "now") -> JSONResponse:
    """LIVE mode: current SWPC pages (now, regions, flares, geo), each block failing alone."""
    try:
        return JSONResponse(live.collect([p for p in panels.split(",") if p]),
                            headers={"Cache-Control": "no-store"})
    except ValueError as e:
        return JSONResponse({"errors": [str(e)]}, status_code=422)


@app.get("/api/timings")
def timing_stats() -> dict:
    """What each source has typically cost on this machine."""
    return {"enabled": timings.enabled(), "sources": timings.get_timings().stats()}


@app.get("/api/cache")
def cache_status() -> dict:
    return {"enabled": resultcache.enabled(), **resultcache.get_cache().stats()}


@app.delete("/api/cache")
def cache_clear() -> dict:
    """Drop everything — useful when checking whether a result is really fresh."""
    resultcache.get_cache().clear()
    return {"cleared": True, **resultcache.get_cache().stats()}


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
