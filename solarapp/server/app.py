from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

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
    src = reg.get(parsed.source)
    try:
        spec = src.fetch(parsed.start, parsed.end)
    except Exception as e:  # surfacing fetch problems to the UI beats a 500
        return JSONResponse({"errors": [f"{src.display_name}: {e}"]}, status_code=502)
    spec["parsed"] = {
        "source": parsed.source,
        "start": parsed.start.isoformat(),
        "end": parsed.end.isoformat(),
    }
    return JSONResponse(spec)
