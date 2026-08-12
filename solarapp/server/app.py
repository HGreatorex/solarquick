from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from solarapp.core import events
from solarapp.core.parser import parse
from solarapp.core.registry import get_registry

app = FastAPI(title="SolarQuick")
STATIC = Path(__file__).parent / "static"


class Query(BaseModel):
    q: str


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

    spec["parsed"] = {
        "source": parsed.source,
        "sources": [r.source for r in parsed.requests],
        "start": parsed.start.isoformat(),
        "end": parsed.end.isoformat(),
    }
    return JSONResponse(spec)
