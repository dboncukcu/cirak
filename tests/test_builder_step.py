import pytest

from cirak import check, ConfigError, register, run
from cirak.api import analyze


def kinds(problems):
    return [problem.kind for problem in problems]


def errors_of(problems):
    return [problem.kind for problem in problems if problem.severity == "error"]


class Module:
    def __init__(self, graph, seed, index, models=None, init=None):
        self.graph = graph
        self.seed = seed
        self.index = index
        self.models = models
        self.init = init

    def __call__(self, x):
        return f"{self.seed}:{self.index}:{x}"


def catalog():
    made = []

    def linear(out_features):
        made.append(out_features)
        return ("linear", out_features)

    register("/layer/test/linear2", linear, kind="layer")
    register("/layer/test/relu2", lambda: "relu", kind="layer")
    register("/builder/test/module", Module, kind="builder")
    register("/std/test/pack", lambda items: items)
    return made


RECIPE = """
blocks:
  net:
    inputs: [x]
    outputs: [y]
    spec:
      - {uri: /layer/test/linear2, params: {out_features: 8}}
      - {uri: /layer/test/relu2}
  composite:
    inputs: [x]
    outputs: [score]
    graph:
      hidden: {model: net, inputs: [x]}
      score: {uri: /layer/test/linear2, params: {out_features: 1}, inputs: [hidden]}
flow:
  outputs: [net, composite, models]
  build_net: {block: net, builder: /builder/test/module, params: {seed: 7, index: 0}, outputs: [net]}
  models: {uri: /std/test/pack, inputs: {items: {net: net}}}
  compose: {block: composite, builder: /builder/test/module, params: {seed: 7, index: 1}, inputs: {models: models},
            outputs: [composite]}
"""


def test_builder_step_builds_the_graph_now_and_calls_the_builder_at_run_time(write):
    made = catalog()
    path = write("a.yaml", RECIPE)
    assert check([path]) == []
    assert made == []
    report = run([path])
    assert made == [8, 1]
    net = report.outputs["net"]
    assert isinstance(net, Module)
    assert (net.seed, net.index, net.models) == (7, 0, None)
    assert [node.name for node in net.graph.nodes] == ["s0", "s1"]
    assert net.graph.nodes[0].obj == ("linear", 8)
    assert net.graph.inputs == ("x",) and net.graph.outputs == ("y",)
    composite = report.outputs["composite"]
    assert composite.models == {"net": net}
    refs = {node.name: node.ref for node in composite.graph.nodes}
    assert refs == {"hidden": "net", "score": None}
    assert report.outputs["models"] == {"net": net}


def test_builder_step_expansion_is_keyed_by_flow_path(write):
    catalog()
    path = write("a.yaml", RECIPE)
    analysis = analyze([path])
    assert set(analysis.expansions) == {"flow.build_net", "flow.compose"}
    assert list(analysis.expansions["flow.build_net"]["graph"]) == ["s0", "s1"]


def test_builder_step_inside_a_flow_block_and_foreach(write):
    catalog()
    path = write("a.yaml", """
blocks:
  net:
    inputs: [x]
    outputs: [y]
    spec:
      - {uri: /layer/test/relu2}
  models:
    variables:
      items: {required: true}
      seed: {required: true}
    flow:
      build:
        foreach: {over: $items$, item: m, key: $m.name$,
                  node: {block: $m.name$, builder: /builder/test/module,
                         params: {seed: $seed$, index: $m.index$}, outputs: [$m.name$]}}
flow:
  outputs: [net]
  models: {block: models, params: {items: [{name: net, index: 3}], seed: 1}}
""")
    assert check([path]) == []
    analysis = analyze([path])
    assert set(analysis.expansions) == {"flow.models.build_net"}
    report = run([path])
    assert (report.outputs["net"].seed, report.outputs["net"].index) == (1, 3)


def test_builder_step_signature_and_kind_checks(write):
    catalog()
    register("/layer/test/notbuilder", lambda graph: graph, kind="layer")
    path = write("a.yaml", """
blocks:
  net:
    inputs: [x]
    outputs: [y]
    spec:
      - {uri: /layer/test/relu2}
flow:
  outputs: [a, b, c, d]
  a: {block: net, builder: /builder/test/module, params: {seed: 7, index: 0, colour: red}, outputs: [a]}
  b: {block: net, builder: /builder/test/module, params: {seed: 7}, inputs: {models: a}, outputs: [b]}
  c: {block: net, builder: /layer/test/notbuilder, outputs: [c]}
  d: {block: ghost, builder: /builder/test/module, params: {seed: 7, index: 0}, outputs: [d], extra: 1}
""")
    problems = check([path])
    found = errors_of(problems)
    assert found.count("signature_mismatch") == 2
    assert "kind_mismatch" in found
    assert "unknown_block" in found
    assert "unknown_key" in found
    messages = [problem.message for problem in problems if problem.kind == "signature_mismatch"]
    assert any("no parameter 'colour'" in message for message in messages)
    assert any("requires parameter 'index'" in message for message in messages)


def test_builder_step_graph_problems_are_located_at_the_step(write):
    catalog()
    path = write("a.yaml", """
blocks:
  broken:
    inputs: [x]
    outputs: [y]
    graph:
      y: {uri: /layer/test/relu2, inputs: [nothing]}
flow:
  outputs: [m]
  m: {block: broken, builder: /builder/test/module, params: {seed: 1, index: 0}, outputs: [m]}
""")
    problems = [problem for problem in check([path]) if problem.kind == "unproduced_input"]
    assert len(problems) == 1
    assert problems[0].line == 10
    assert "flow.m" in problems[0].message


def test_builder_step_default_output_is_its_name(write):
    catalog()
    path = write("a.yaml", """
blocks:
  net:
    inputs: [x]
    outputs: [y]
    spec:
      - {uri: /layer/test/relu2}
flow:
  outputs: [net]
  net: {block: net, builder: /builder/test/module, params: {seed: 7, index: 0}}
""")
    assert check([path]) == []
    assert isinstance(run([path]).outputs["net"], Module)
