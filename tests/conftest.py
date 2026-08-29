import pytest

from cirak.registry import registry
from cirak.std import register_std

CATALOG = [
    "/a/b/c",
    "/a/b/d",
    "/b/p/x",
    "/c/p/above",
    "/c/p/d",
    "/c/p/balanced",
    "/builder/proj/compose",
    "/stat/proj/mean",
    "/stat/proj/combine",
    "/series/statlib/rolling_mean",
    "/series/statlib/savgol",
    "/series/proj/apply",
    "/io/proj/read_csv",
    "/io/proj/write_json",
]


def anything(*args, **kwargs):
    return args, kwargs


@pytest.fixture(autouse=True)
def catalog():
    saved_entries = dict(registry._entries)
    saved_resolved = dict(registry._resolved)
    registry._entries.clear()
    registry._resolved.clear()
    for uri in CATALOG:
        registry.register(uri, anything, description="test catalog target")
    register_std(registry)
    yield registry
    registry._entries.clear()
    registry._resolved.clear()
    registry._entries.update(saved_entries)
    registry._resolved.update(saved_resolved)


@pytest.fixture
def write(tmp_path):
    def _write(name, text):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return str(target)
    return _write
