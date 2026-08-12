from __future__ import annotations

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
    return FileResponse(STATIC / "index.html")


@app.get("/api/sources")
def sources() -> list[dict]:
    return [
        {"name": s.name, "display_name": s.display_name, "keywords": s.keywords}
        for s in get_registry().sources.values()
    ]


@app.post("/api/query")
def query(body: Query) -> JSONResponse:
    reg = get_registry()
    parsed = parse(body.q, reg)
    if not parsed.ok:
        return JSONResponse({"errors": parsed.errors}, status_code=422)

    try:  # event queries ("biggest flare since 2014") resolve against HEK here
        events.expand(parsed)
    except RuntimeError as e:
        return JSONResponse({"errors": [str(e)]}, status_code=422)

    def run(req) -> tuple[dict | None, str | None]:
        src = reg.get(req.source)
        try:
            return src.fetch(parsed.start, parsed.end, **req.extras), None
        except Exception as e:  # surfacing fetch problems to the UI beats a 500
            return None, f"{src.display_name}: {e}"

    # sources are network-bound and independent — fetch them concurrently
    with ThreadPoolExecutor(max_workers=min(4, len(parsed.requests))) as pool:
        outcomes = list(pool.map(run, parsed.requests))
    specs = [s for s, _ in outcomes if s is not None]
    errors = [e for _, e in outcomes if e is not None]

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
