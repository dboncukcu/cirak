import sys

import pytest

from cirak import RegistryError, check, register_fragment
from cirak.registry import Registry


def test_description_defaults_to_signature():
    fresh = Registry()

    @fresh.register("/a/b/c")
    def read_csv(path, headers: bool = True):
        return path, headers

    assert fresh.lookup("/a/b/c").description == "read_csv(path, headers: bool = True)"


def test_description_defaults_for_lazy_and_fragment():
    fresh = Registry()
    fresh.register("/a/b/lazy", "json:dumps")
    assert fresh.lookup("/a/b/lazy").description == "json:dumps"
    fresh.register_fragment("/flow/x/z", "/somewhere/x.yaml")
    assert fresh.lookup("/flow/x/z").description == "/somewhere/x.yaml"


def test_explicit_description_wins():
    fresh = Registry()

    def fn():
        return 7

    fresh.register("/a/b/c", fn, description="does a thing")
    assert fresh.lookup("/a/b/c").description == "does a thing"


def test_uri_shape():
    fresh = Registry()
    for bad in ["a/b/c", "/a/b", "/A/b/c", "/a/b/c-d", "/a//c"]:
        with pytest.raises(RegistryError):
            fresh.register(bad, object(), description="x")
    fresh.register("/a/b/c/deep_name", object(), description="x")


def test_identical_reregistration_is_silent():
    fresh = Registry()

    def fn():
        return 7

    fresh.register("/a/b/c", fn, description="d")
    fresh.register("/a/b/c", fn, description="d")
    with pytest.raises(RegistryError):
        fresh.register("/a/b/c", fn, description="different")
    with pytest.raises(RegistryError):
        fresh.register("/a/b/c", object(), description="d")


def test_decorator_form():
    fresh = Registry()

    @fresh.register("/a/b/c", description="d")
    def fn():
        return 7

    assert fresh.resolve("/a/b/c") is fn


def test_register_many_with_and_without_description():
    fresh = Registry()
    fresh.register_many("/layer/torch", {
        "linear": ("json:dumps", "serialize"),
        "loads": "json:loads",
    })
    assert fresh.lookup("/layer/torch/linear").description == "serialize"
    assert fresh.lookup("/layer/torch/loads").description == "json:loads"
    with pytest.raises(RegistryError):
        fresh.register_many("/a/b", {"x": ("json:dumps", "d", "extra")})


def test_bad_import_string():
    fresh = Registry()
    with pytest.raises(RegistryError):
        fresh.register("/a/b/c", "no_colon_here", description="d")


def test_lazy_import(tmp_path, monkeypatch):
    module = tmp_path / "lazy_probe_mod.py"
    module.write_text("def target(x):\n    return x * 2\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("lazy_probe_mod", None)
    fresh = Registry()
    fresh.register("/num/probe/double", "lazy_probe_mod:target", description="doubles")
    assert "lazy_probe_mod" not in sys.modules
    target = fresh.resolve("/num/probe/double")
    assert "lazy_probe_mod" in sys.modules
    assert target(4) == 8
    assert fresh.resolve("/num/probe/double") is target
    sys.modules.pop("lazy_probe_mod", None)


def test_fragment_is_not_callable():
    fresh = Registry()
    fresh.register_fragment("/flow/x/y", "/somewhere/x.yaml", description="d")
    with pytest.raises(RegistryError):
        fresh.resolve("/flow/x/y")


def test_fragment_registration_and_include(write):
    fragment = write("frag.yaml", "alias:\n  fast: /series/statlib/rolling_mean\n")
    register_fragment("/flow/test/frag", fragment, description="alias pack")
    main = write("main.yaml", "include: [/flow/test/frag]\nuser:\n  uri: fast\n  partial: true\n")
    failures = [problem for problem in check([main]) if problem.severity == "error"]
    assert failures == []


def test_ls_boundary_and_search():
    fresh = Registry()
    fresh.register("/stat/proj/mean", object(), description="mean value")
    fresh.register("/statistics/x/y", object(), description="other")
    assert [entry.uri for entry in fresh.ls("/stat")] == ["/stat/proj/mean"]
    assert [entry.uri for entry in fresh.search("MEAN")] == ["/stat/proj/mean"]
    assert len(fresh.ls("/")) == 2
