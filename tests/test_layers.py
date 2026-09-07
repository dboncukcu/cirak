from pathlib import Path

from cirak import check, resolve
from cirak.api import analyze, annotated, layers, write_resolved
from cirak.loader import load, parse_value
from cirak.merge import merge_layers


def kinds(problems):
    return [problem.kind for problem in problems]


def names(layer):
    return [Path(loaded.file).name for loaded in layer.walk()]


def test_included_files_form_layers_below_the_includer(write):
    write("c.yaml", "params:\n  n: 1\n  tag: c\n")
    write("b.yaml", "include: [c.yaml]\nparams:\n  n: 2\n  other: b\n")
    main = write("a.yaml", "include: [b.yaml]\nparams:\n  tag: a\n")
    layer, problems = load([main])
    assert problems == []
    assert names(layer) == ["c.yaml", "b.yaml", "a.yaml"]
    assert [Path(entry.files[0].file).name for _, entry in layer.ordered()] == ["c.yaml", "b.yaml", "a.yaml"]
    assert [entry.included_by and Path(entry.included_by).name for _, entry in layer.ordered()] == ["b.yaml", "a.yaml", None]
    data, provenance, overrides, merge_problems = merge_layers(layer)
    assert merge_problems == []
    assert data["params"] == {"n": 2, "tag": "a", "other": "b"}
    assert provenance[("params", "n")].file.endswith("b.yaml")
    assert provenance[("params", "tag")].file.endswith("a.yaml")
    assert [(path, Path(winner.file).name, Path(loser.file).name) for path, winner, loser in overrides] == [
        (("params", "n"), "b.yaml", "c.yaml"),
        (("params", "tag"), "a.yaml", "c.yaml"),
    ]


def test_include_list_order_is_bottom_to_top(write):
    write("low.yaml", "params:\n  n: 1\n")
    write("high.yaml", "params:\n  n: 2\n")
    main = write("a.yaml", "include: [low.yaml, high.yaml]\n")
    assert resolve([main])["params"]["n"] == 2
    other = write("b.yaml", "include: [high.yaml, low.yaml]\n")
    assert resolve([other])["params"]["n"] == 1


def test_lists_and_scalars_are_replaced_whole_mappings_merge(write):
    write("base.yaml", "training:\n  stop: [a, b]\n  epochs: 10\n  deep: {x: 1, y: 2}\n")
    main = write("top.yaml", "include: [base.yaml]\ntraining:\n  stop: []\n  deep: {y: 3}\n")
    data = resolve([main])
    assert data["training"] == {"stop": [], "epochs": 10, "deep": {"x": 1, "y": 3}}


def test_type_change_between_layers_replaces_the_subtree(write):
    write("base.yaml", "thing:\n  deep: 1\n")
    main = write("top.yaml", "include: [base.yaml]\nthing: 5\n")
    layer, _ = load([main])
    data, provenance, overrides, problems = merge_layers(layer)
    assert problems == []
    assert data["thing"] == 5
    assert ("thing", "deep") not in provenance
    assert [path for path, _, _ in overrides] == [("thing",)]


def test_same_file_reached_twice_loads_once(write):
    write("common.yaml", "params:\n  n: 3\n")
    write("b.yaml", "include: [common.yaml]\nparams:\n  b: 1\n")
    write("c.yaml", "include: [common.yaml]\nparams:\n  c: 1\n")
    main = write("a.yaml", "include: [b.yaml, c.yaml]\n")
    layer, problems = load([main])
    assert problems == []
    assert names(layer) == ["common.yaml", "b.yaml", "c.yaml", "a.yaml"]
    data, _, overrides, _ = merge_layers(layer)
    assert data["params"] == {"n": 3, "b": 1, "c": 1}
    assert overrides == []


def test_command_line_files_are_one_strict_layer(write):
    a = write("a.yaml", "params:\n  n: 1\n")
    b = write("b.yaml", "params:\n  n: 1\n")
    assert kinds(check([a, b])) == ["merge_conflict"]
    write("base.yaml", "params:\n  n: 1\n")
    top = write("top.yaml", "include: [base.yaml]\nparams:\n  n: 1\n")
    assert check([top]) == []


def test_plugins_pool_across_layers(write):
    write("base.yaml", "plugins: [one, two]\n")
    main = write("top.yaml", "include: [base.yaml]\nplugins: [two, three]\n")
    layer, _ = load([main])
    data, _, _, _ = merge_layers(layer)
    assert data["plugins"] == ["one", "two", "three"]


def test_set_is_the_top_layer(write):
    write("base.yaml", "params:\n  n: 1\ntraining:\n  epochs: 10\n  stop: [x]\n")
    main = write("top.yaml", "include: [base.yaml]\nparams:\n  n: 2\n")
    data = resolve([main], sets=[("params.n", 7), ("training.stop", []), ("training.new.deep", "v")])
    assert data["params"]["n"] == 7
    assert data["training"] == {"epochs": 10, "stop": [], "new": {"deep": "v"}}
    analysis = analyze([main], sets=[("params.n", 7)])
    assert analysis.provenance[("params", "n")].file == "--set"
    losers = [Path(loser.file).name for path, winner, loser in analysis.overrides if path == ("params", "n")]
    assert losers == ["base.yaml", "top.yaml"]


def test_parse_value_reads_yaml():
    assert parse_value("5") == 5
    assert parse_value("null") is None
    assert parse_value("[]") == []
    assert parse_value("runs/x") == "runs/x"
    assert parse_value("{a: 1}") == {"a": 1}
    assert parse_value("true") is True


def test_resolved_dump_comments_every_overridden_leaf(write, tmp_path):
    write("base.yaml", "params:\n  n: 1\n  keep: 0\n")
    main = write("top.yaml", "include: [base.yaml]\nparams:\n  n: 2\n")
    analysis = analyze([main], sets=[("params.n", 3)])
    record = tmp_path / "rec"
    write_resolved(analysis, record)
    text = (record / "resolved.yaml").read_text()
    assert "n: 3  # --set overrides base.yaml" not in text
    line = [line for line in text.splitlines() if line.startswith("  n:")][0]
    assert line.startswith("  n: 3")
    assert "--set overrides" in line
    assert "base.yaml:2" in line and "top.yaml:3" in line
    assert "keep: 0\n" in text
    rerun = resolve([str(record / "resolved.yaml")])
    assert rerun["params"] == {"n": 3, "keep": 0}


def test_annotated_tree_keeps_lists_and_scalars(write):
    main = write("a.yaml", "params:\n  xs: [1, 2]\n  s: text\n")
    analysis = analyze([main])
    tree = annotated(analysis.data, analysis.overrides)
    assert list(tree["params"]["xs"]) == [1, 2]
    assert tree["params"]["s"] == "text"


def test_layers_text_lists_files_bottom_to_top_with_overrides(write):
    write("c.yaml", "params:\n  n: 1\n")
    write("b.yaml", "include: [c.yaml]\nparams:\n  n: 2\n")
    main = write("a.yaml", "include: [b.yaml]\nparams:\n  n: 3\n")
    text = layers([main], sets=[("params.n", 4)])
    lines = text.splitlines()
    assert lines[0] == "layers, bottom to top:"
    assert lines[1] == "  1. c.yaml (included by b.yaml)"
    assert lines[2] == "  2. b.yaml (included by a.yaml)"
    assert "overrides params.n (" in lines[3] and "c.yaml:2" in lines[3]
    assert lines[4] == "  3. a.yaml"
    assert "b.yaml:3" in lines[5]
    assert lines[6] == "  4. --set"
    assert "a.yaml:3" in lines[7]


def test_included_layer_cannot_conflict_with_itself(write):
    write("dup.yaml", "a: 1\na: 2\n")
    main = write("top.yaml", "include: [dup.yaml]\n")
    assert kinds(check([main])) == ["duplicate_key"]


def test_a_lego_record_written_above_replaces_the_call_below(write):
    write("base.yaml", """
training:
  checkpoint: {uri: /a/b/c, params: {monitor: val/rmse, mode: min}}
  turn: {uri: /a/b/c, params: {order: [d, g]}}
  deep: {batch: {size: 128, workers: 2}}
model: {block: net, params: {width: 64}, builder: /b/p/x}
""")
    main = write("top.yaml", """
include: [base.yaml]
training:
  checkpoint: {uri: /a/b/d}
  turn: {params: {steps: {d: 5}}}
  deep: {batch: {size: 32}}
model: {block: wide, builder: /b/p/x}
""")
    layer, _ = load([main])
    data, provenance, overrides, problems = merge_layers(layer)
    assert problems == []
    assert data["training"]["checkpoint"] == {"uri": "/a/b/d"}
    assert data["training"]["turn"] == {"uri": "/a/b/c", "params": {"order": ["d", "g"], "steps": {"d": 5}}}
    assert data["training"]["deep"] == {"batch": {"size": 32, "workers": 2}}
    assert data["model"] == {"block": "wide", "builder": "/b/p/x"}
    assert ("training", "checkpoint", "params") not in provenance
    assert provenance[("training", "turn", "params", "steps", "d")].file.endswith("top.yaml")
    assert [path for path, _, _ in overrides] == [("training", "checkpoint"), ("training", "deep", "batch", "size"),
                                                   ("model",)]


def test_set_path_merges_into_the_existing_call(write):
    main = write("a.yaml", """
data:
  split: {uri: /a/b/c, params: {k: 5, fold: 0, val: 0.15, seed: 7}}
""")
    data = resolve([main], sets=[("data.split.params.val", None), ("data.split.params.fold", 2)])
    assert data["data"]["split"] == {"uri": "/a/b/c", "params": {"k": 5, "fold": 2, "val": None, "seed": 7}}
    replaced = resolve([main], sets=[("data.split", {"uri": "/a/b/d"})])
    assert replaced["data"]["split"] == {"uri": "/a/b/d"}


def test_a_lego_record_written_twice_in_one_layer_is_a_conflict(write):
    a = write("a.yaml", "saver: {uri: /a/b/c, params: {monitor: x}}\n")
    b = write("b.yaml", "saver: {uri: /a/b/d}\n")
    assert [problem.kind for problem in check([a, b]) if problem.severity == "error"] == ["merge_conflict"]
    c = write("c.yaml", "saver: {uri: /a/b/c, params: {monitor: x}}\n")
    d = write("d.yaml", "saver: {params: {mode: max}}\n")
    assert [problem.kind for problem in check([c, d]) if problem.severity == "error"] == []
    assert resolve([c, d])["saver"] == {"uri": "/a/b/c", "params": {"monitor": "x", "mode": "max"}}
