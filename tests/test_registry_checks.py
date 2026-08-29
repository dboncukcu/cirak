import sys

from cirak import check, register


def kinds(problems):
    return [problem.kind for problem in problems]


def test_unknown_uri_with_suggestion(write):
    path = write("a.yaml", "c: {uri: /stat/proj/meen, partial: true}\n")
    found = [problem for problem in check([path]) if problem.kind == "unknown_uri"]
    assert len(found) == 1
    assert "/stat/proj/mean" in (found[0].hint or "")


def test_unknown_builder_uri(write):
    path = write("a.yaml", """
blocks:
  chain:
    spec:
      - {uri: /a/b/c, partial: true}
c:
  block: chain
  builder: /builder/proj/missing
""")
    assert "unknown_uri" in kinds(check([path]))


def test_unknown_condition_uri(write):
    path = write("a.yaml", """
flow:
  clean: {uri: /a/b/c, inputs: [raw], outputs: []}
  gated:
    uri: /a/b/c
    inputs: [raw]
    outputs: []
    when: /kosul/proj/ghost
""")
    assert "unknown_uri" in kinds(check([path]))


def test_signature_unknown_param(write):
    def fn(table):
        return table

    register("/tbl/test/fn", fn, description="d")
    path = write("a.yaml", "c: {uri: /tbl/test/fn, partial: true, params: {tabel: 1}}\n")
    assert "signature_mismatch" in kinds(check([path]))


def test_signature_missing_required(write):
    def fn(table, limit):
        return table, limit

    register("/tbl/test/fn2", fn, description="d")
    path = write("a.yaml", "c: {uri: /tbl/test/fn2, params: {limit: 1}}\n")
    assert "signature_mismatch" in kinds(check([path]))


def test_partial_skips_missing_required(write):
    def fn(table, limit):
        return table, limit

    register("/tbl/test/fn3", fn, description="d")
    path = write("a.yaml", "c: {uri: /tbl/test/fn3, partial: true, params: {limit: 1}}\n")
    assert "signature_mismatch" not in kinds(check([path]))


def test_node_signatures_checked(write):
    def fn(value, step):
        return value + step

    register("/num/test/inc2", fn, description="d")
    path = write("a.yaml", """
blocks:
  chain:
    spec:
      - {uri: /num/test/inc2, partial: true, params: {stepp: 1}}
c:
  block: chain
  builder: /builder/proj/compose
""")
    assert "signature_mismatch" in kinds(check([path]))


def test_var_keyword_targets_accept_anything(write):
    path = write("a.yaml", "c: {uri: /a/b/c, params: {whatever: 1, extra: 2}}\n")
    assert "signature_mismatch" not in kinds(check([path]))


def test_plugin_import_failed(write):
    path = write("a.yaml", "plugins: [definitely_missing_module_xyz]\n")
    assert "plugin_import_failed" in kinds(check([path]))


def test_plugin_next_to_recipe_found_without_syspath(write):
    write("neighbor_probe_mod.py",
          "import cirak\n\n"
          "def neighbor(x):\n"
          "    return x\n\n"
          "cirak.register('/num/neighbor/helper', neighbor, description='next to recipe')\n")
    sys.modules.pop("neighbor_probe_mod", None)
    path = write("a.yaml",
                 "plugins: [neighbor_probe_mod]\nc: {uri: /num/neighbor/helper, partial: true}\n")
    failures = [problem for problem in check([path]) if problem.severity == "error"]
    assert failures == []
    sys.modules.pop("neighbor_probe_mod", None)


def test_plugin_registers_target(write, tmp_path, monkeypatch):
    module = tmp_path / "plugin_probe_mod.py"
    module.write_text(
        "import cirak\n\n"
        "def helper(x):\n"
        "    return x\n\n"
        "cirak.register('/num/plugin/helper', helper, description='from plugin')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("plugin_probe_mod", None)
    path = write("a.yaml",
                 "plugins: [plugin_probe_mod]\nc: {uri: /num/plugin/helper, partial: true}\n")
    failures = [problem for problem in check([path]) if problem.severity == "error"]
    assert failures == []
    sys.modules.pop("plugin_probe_mod", None)
