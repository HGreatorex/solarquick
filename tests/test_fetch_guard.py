"""Download-failure guard and GOES satellite selection (tsutil).

Archives 503 intermittently. Without a guard, an all-failed download hands
sunpy an empty file list and the user sees "pop from empty list", which is
what broke the first pre-SDO flare event view.
"""
import pytest

from solarapp.core import tsutil


class FakeResults(list):
    """Stands in for parfive.Results: a list of paths plus .errors."""

    def __init__(self, paths=(), errors=()):
        super().__init__(paths)
        self.errors = list(errors)


class FakeError:
    def __init__(self, status=None, exception=None):
        if status is not None:
            self.response = type("R", (), {"status": status})()
        self.exception = exception


@pytest.fixture
def fake_fido(monkeypatch):
    """Install a fake sunpy.net.Fido whose fetch() replays a script of results.

    The returned list records one entry per fetch call as (query, kwargs).
    """
    calls = []

    def install(*results):
        script = list(results)

        def fetch(query, **kwargs):
            calls.append((query, kwargs))
            return script[min(len(calls) - 1, len(script) - 1)]

        module = type("Fido", (), {"fetch": staticmethod(fetch)})
        monkeypatch.setitem(
            __import__("sys").modules, "sunpy.net",
            type("M", (), {"Fido": module})(),
        )
        return calls

    return install


def test_returns_files_on_success(fake_fido):
    fake_fido(FakeResults(["/tmp/a.nc"]))
    assert tsutil.fetch_files("Q", "GOES XRS") == ["/tmp/a.nc"]


def test_retries_partial_failure(fake_fido):
    calls = fake_fido(
        FakeResults(["/tmp/a.nc"], [FakeError(status=503)]),  # first attempt
        FakeResults(["/tmp/a.nc", "/tmp/b.nc"]),             # retry succeeds
    )
    assert tsutil.fetch_files("Q", "GOES XRS") == ["/tmp/a.nc", "/tmp/b.nc"]
    assert len(calls) == 2  # stops retrying once clean


def test_gives_up_after_retry_budget(fake_fido):
    calls = fake_fido(FakeResults([], [FakeError(status=503)]))  # always fails
    with pytest.raises(RuntimeError):
        tsutil.fetch_files("Q", "GOES XRS", retries=3)
    assert len(calls) == 4  # initial attempt + 3 retries


def test_downloads_are_single_part(fake_fido):
    # multi-part splitting on tens-of-KB files only adds failure modes
    calls = fake_fido(FakeResults(["/tmp/a.nc"]))
    tsutil.fetch_files("Q", "GOES XRS")
    assert calls[0][1].get("max_splits") == 1


def test_no_retry_when_clean(fake_fido):
    calls = fake_fido(FakeResults(["/tmp/a.nc"]))
    tsutil.fetch_files("Q", "GOES XRS")
    assert len(calls) == 1


def test_all_failed_raises_readable_error(fake_fido):
    fake_fido(FakeResults([], [FakeError(status=503)]))
    with pytest.raises(RuntimeError) as ei:
        tsutil.fetch_files("Q", "GOES XRS")
    msg = str(ei.value)
    assert "GOES XRS" in msg
    assert "HTTP 503" in msg
    assert "transient" in msg
    assert "pop from empty list" not in msg


def test_error_hint_falls_back_to_exception_name(fake_fido):
    fake_fido(FakeResults([], [FakeError(exception=TimeoutError())]))
    with pytest.raises(RuntimeError, match="TimeoutError"):
        tsutil.fetch_files("Q", "RHESSI")


def test_empty_without_errors_still_raises(fake_fido):
    fake_fido(FakeResults([], []))
    with pytest.raises(RuntimeError, match="none could be downloaded"):
        tsutil.fetch_files("Q", "Fermi GBM")


# -- GOES satellite selection -------------------------------------------

class FakeTable(list):
    colnames = ["SatelliteNumber"]

    def __getitem__(self, key):
        if isinstance(key, list):
            return FakeTable([list.__getitem__(self, i) for i in key])
        return list.__getitem__(self, key)


def test_picks_highest_satellite():
    # 2003 returns GOES-10 and GOES-12; only the newer should be fetched
    table = FakeTable([{"SatelliteNumber": 10}, {"SatelliteNumber": 12}])
    picked = tsutil.pick_satellite([table])
    assert [row["SatelliteNumber"] for row in picked] == [12]


def test_single_satellite_passthrough():
    table = FakeTable([{"SatelliteNumber": 16}])
    query = [table]
    assert tsutil.pick_satellite(query) is query


def test_keeps_all_rows_of_chosen_satellite():
    # a multi-day range gives several files per satellite
    table = FakeTable([
        {"SatelliteNumber": 10}, {"SatelliteNumber": 12},
        {"SatelliteNumber": 10}, {"SatelliteNumber": 12},
    ])
    picked = tsutil.pick_satellite([table])
    assert [row["SatelliteNumber"] for row in picked] == [12, 12]


def test_missing_column_passthrough():
    class NoColumn(list):
        colnames = ["filename"]
    query = [NoColumn([{"filename": "a"}, {"filename": "b"}])]
    assert tsutil.pick_satellite(query) is query


# -- search window -------------------------------------------------------

def test_parfive_timeouts_are_bounded():
    # parfive's defaults are "no total timeout" + 90 s socket read, which turned
    # one stalled NCEI connection into a 185 s request
    import os
    import solarapp.core.tsutil  # noqa: F401  (sets the defaults on import)
    assert 0 < int(os.environ["PARFIVE_TOTAL_TIMEOUT"]) <= 300
    assert 0 < int(os.environ["PARFIVE_SOCK_READ_TIMEOUT"]) <= 60
