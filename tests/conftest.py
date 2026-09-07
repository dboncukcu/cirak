import pytest

from cirak.registry import registry
from cirak.std import register_std

KINDS = ("source", "split", "pre", "feed", "loader", "layer", "init", "criterion", "objective", "metric",
         "adapter", "optimizer", "schedule", "turn", "trigger", "checkpoint", "rule", "generate", "plot", "lego")

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
    saved_kinds = list(registry._kinds)
    registry._entries.clear()
    registry._resolved.clear()
    registry.declare_kinds(*KINDS)
    for uri in CATALOG:
        registry.register(uri, anything, description="test catalog target")
    register_std(registry)
    yield registry
    registry._entries.clear()
    registry._resolved.clear()
    registry._kinds[:] = saved_kinds
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
