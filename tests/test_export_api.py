"""The /api/export endpoint. The spec is client-supplied, so malformed input
must come back as a 4xx — never a 500."""
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from solarapp.server.app import app  # noqa: E402

client = fastapi_testclient.TestClient(app)

SPEC = {
    "title": "GOES XRS  2026-08-11 00:00 – 2026-08-12 00:00 UT",
    "series": [{"name": "XRS-B 1–8 Å",
                "times": ["2026-08-11T00:00:00+00:00"], "values": [4.5e-7]}],
    "provenance": "NOAA SWPC realtime (primary GOES)",
}


def post(spec, fmt="csv", query=None):
    return client.post("/api/export",
                       json={"spec": spec, "format": fmt, "query": query})


def test_csv_download_headers():
    r = post(SPEC, query="goes sxr yesterday")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="solarquick_goes_xrs_2026-08-11.csv"' in \
        r.headers["content-disposition"]
    assert "goes sxr yesterday" in r.text
    assert "XRS-B 1–8 Å" in r.text


def test_json_download_roundtrips():
    import json
    r = post(SPEC, fmt="json", query="goes sxr yesterday")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert ".json" in r.headers["content-disposition"]
    data = json.loads(r.text)
    assert data["query"] == "goes sxr yesterday"
    assert data["series"][0]["name"] == "XRS-B 1–8 Å"


def test_unsupported_format_is_400():
    assert post(SPEC, fmt="pdf").status_code == 400


def test_format_is_case_insensitive():
    assert post(SPEC, fmt="CSV").status_code == 200


def test_images_refused_with_reason():
    r = post({"kind": "images", "title": "AIA 304", "images": [{"url": "u"}]})
    assert r.status_code == 422
    assert "no tabular data" in r.json()["errors"][0]


@pytest.mark.parametrize("bad", [
    {"title": "t", "series": "not-a-list"},
    {"title": "t", "series": [{"name": "a", "times": "nope", "values": 1}]},
    {"kind": "spectrogram", "title": "t", "times": ["a"], "freqs": ["b"], "z": "no"},
    {"kind": "events", "title": "t", "events": ["not-a-dict"]},
    {"kind": "positions", "title": "t", "bodies": [42]},
    {"title": "t", "series": [[1, 2]]},
    {"kind": "panels", "title": "t", "panels": "nope"},
])
def test_malformed_specs_never_500(bad):
    # garbage in, garbage or a 4xx out — but never a server fault. (Some of
    # these are structurally odd yet still writable, which is fine.)
    r = post(bad)
    assert r.status_code != 500, f"500 for {bad}"


def test_nulls_roundtrip():
    # ragged series pad with null, and JSON.stringify sends NaN as null —
    # both must export cleanly rather than erroring
    spec = {"title": "t", "series": [
        {"name": "a", "times": ["t0", "t1"], "values": [1.0, None]}]}
    assert post(spec).status_code == 200
    assert post(spec, fmt="json").status_code == 200


def test_empty_spec_is_4xx():
    assert 400 <= post({}).status_code < 500
