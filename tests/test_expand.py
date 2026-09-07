from cirak.expand import expand
from cirak.loader import load
from cirak.merge import merge_layers
from cirak.resolve import resolve


def kinds(problems):
    return [problem.kind for problem in problems]


def expanded(paths):
    layer, load_problems = load(paths)
    data, provenance, _, merge_problems = merge_layers(layer)
    data, resolve_problems = resolve(data, provenance)
    assert load_problems == merge_problems == resolve_problems == []
    return expand(data, provenance)


SPEC_RECIPE = """
blocks:
  chain:
    variables:
      window: {default: 3}
      passes: {required: true}
    spec:
      - uri: /series/statlib/rolling_mean
        partial: true
        params: {window: $window$}
        repeat: $passes$
smoother:
  block: chain
  params: {passes: 2, window: 5}
  builder: /builder/proj/compose
"""


def test_spec_chain(write):
    path = write("a.yaml", SPEC_RECIPE)
    expansions, problems = expanded([path])
    assert problems == []
    graph = expansions["smoother"]
    assert graph["inputs"] == ["s_in"]
    assert graph["outputs"] == ["s1"]
    assert list(graph["graph"]) == ["s0", "s1"]
    assert graph["graph"]["s0"]["inputs"] == ["s_in"]
    assert graph["graph"]["s1"]["inputs"] == ["s0"]
    assert graph["graph"]["s0"]["params"] == {"window": 5}
    assert graph["graph"]["s0"]["params"] is not graph["graph"]["s1"]["params"]


def test_default_variable_applies(write):
    path = write("a.yaml", SPEC_RECIPE.replace("passes: 2, window: 5", "passes: 1"))
    expansions, problems = expanded([path])
    assert problems == []
    assert expansions["smoother"]["graph"]["s0"]["params"] == {"window": 3}


def test_repeat_zero_drops(write):
    path = write("a.yaml", SPEC_RECIPE.replace("passes: 2", "passes: 0"))
    expansions, problems = expanded([path])
    assert problems == []
    graph = expansions["smoother"]
    assert graph["graph"] == {}
    assert graph["outputs"] == ["s_in"]


def test_bad_repeat(write):
    path = write("a.yaml", SPEC_RECIPE.replace("passes: 2", "passes: yikes"))
    expansions, problems = expanded([path])
    assert kinds(problems) == ["bad_repeat"]


def test_missing_variable(write):
    path = write("a.yaml", SPEC_RECIPE.replace("  params: {passes: 2, window: 5}\n", ""))
    expansions, problems = expanded([path])
    assert kinds(problems) == ["missing_variable"]


def test_unknown_param(write):
    path = write("a.yaml", SPEC_RECIPE.replace("passes: 2, window: 5", "passes: 2, wrong: 5"))
    expansions, problems = expanded([path])
    assert kinds(problems) == ["unknown_param"]


GRAPH_RECIPE = """
blocks:
  chain:
    variables:
      passes: {required: true}
    spec:
      - uri: /series/statlib/rolling_mean
        partial: true
        repeat: $passes$
  stats:
    inputs: [series]
    outputs: [report]
    graph:
      smooth: {block: chain, params: {passes: 2}, inputs: series}
      mean: {uri: /stat/proj/mean, partial: true, inputs: smooth}
      report: {uri: /stat/proj/combine, partial: true, inputs: [mean, smooth]}
summarizer:
  block: stats
  builder: /builder/proj/compose
"""


def test_nested_block_gets_dotted_names(write):
    path = write("a.yaml", GRAPH_RECIPE)
    expansions, problems = expanded([path])
    assert problems == []
    graph = expansions["summarizer"]["graph"]
    assert list(graph) == ["smooth.s0", "smooth.s1", "mean", "report"]
    assert graph["smooth.s0"]["inputs"] == ["series"]
    assert graph["smooth.s1"]["outputs"] == ["smooth"]
    assert graph["mean"]["inputs"] == ["smooth"]
    assert graph["report"]["inputs"] == ["mean", "smooth"]


def test_block_cycle(write):
    path = write("a.yaml", """
blocks:
  a:
    spec:
      - {block: b}
  b:
    spec:
      - {block: a}
c:
  block: a
  builder: /b/p/x
""")
    expansions, problems = expanded([path])
    assert "block_cycle" in kinds(problems)


def test_unknown_block(write):
    path = write("a.yaml", "c:\n  block: ghost\n  builder: /b/p/x\n")
    expansions, problems = expanded([path])
    assert kinds(problems) == ["unknown_block"]


def test_repeat_in_graph_chains_copies(write):
    path = write("a.yaml", """
blocks:
  deep:
    inputs: [x]
    outputs: [y]
    graph:
      y: {uri: /a/b/c, partial: true, inputs: x, repeat: 2}
c:
  block: deep
  builder: /b/p/x
""")
    expansions, problems = expanded([path])
    assert problems == []
    graph = expansions["c"]["graph"]
    assert list(graph) == ["y_0", "y_1"]
    assert graph["y_0"]["inputs"] == ["x"] and graph["y_0"]["outputs"] == ["y_0"]
    assert graph["y_1"]["inputs"] == ["y_0"] and graph["y_1"]["outputs"] == ["y"]
