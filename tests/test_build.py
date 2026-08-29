import pytest

from cirak import BuildError, register
from cirak.api import analyze
from cirak.build import build_components


def store_for(paths):
    analysis = analyze(paths)
    assert [problem for problem in analysis.problems if problem.severity == "error"] == []
    return build_components(analysis.data, analysis.expansions)


def test_block_component_built_through_builder(write):
    def inc(value, step):
        return value + step

    def compose(graph):
        def composed(*args):
            values = dict(zip(graph.inputs, args))
            for node in graph.nodes:
                values[node.outputs[0]] = node.obj(*[values[key] for key in node.inputs])
            return values[graph.outputs[0]]
        return composed

    register("/num/test/inc", inc, description="add step")
    register("/builder/test/compose", compose, description="chain graph")
    path = write("a.yaml", """
blocks:
  chain:
    variables:
      step: {required: true}
    spec:
      - uri: /num/test/inc
        partial: true
        params: {step: $step$}
        repeat: 2
adder:
  block: chain
  params: {step: 10}
  builder: /builder/test/compose
""")
    adder = store_for([path]).get("adder")
    assert adder(5) == 25


def test_graph_nodes_are_topologically_ordered(write):
    def passthrough(value):
        return value

    def names_builder(graph):
        return [node.name for node in graph.nodes]

    register("/num/test/pass1", passthrough, description="identity")
    register("/builder/test/names", names_builder, description="node names")
    path = write("a.yaml", """
blocks:
  net:
    inputs: [x]
    outputs: [c]
    graph:
      c: {uri: /num/test/pass1, partial: true, inputs: b}
      b: {uri: /num/test/pass1, partial: true, inputs: a}
      a: {uri: /num/test/pass1, partial: true, inputs: x}
model:
  block: net
  builder: /builder/test/names
""")
    assert store_for([path]).get("model") == ["a", "b", "c"]


def test_references_groups_escapes_and_singletons(write):
    def factory(value, factor):
        return value * factor

    def pick(fns, one, label):
        return fns, one, label

    register("/num/test/factory", factory, description="multiply")
    register("/num/test/pick", pick, description="collect")
    path = write("a.yaml", """
tools:
  double: {uri: /num/test/factory, partial: true, params: {factor: 2}}
  triple: {uri: /num/test/factory, partial: true, params: {factor: 3}}
consumer:
  uri: /num/test/pick
  params: {fns: "@tools", one: "@tools.double", label: "@@literal"}
""")
    store = store_for([path])
    fns, one, label = store.get("consumer")
    assert list(fns) == ["double", "triple"]
    assert one is fns["double"]
    assert label == "@literal"
    assert store.get("tools.double") is one
    assert one(4) == 8
    assert fns["triple"](4) == 12


def test_partial_semantics(write):
    def fn(a, b):
        return a + b

    register("/num/test/fn", fn, description="adds")
    path = write("a.yaml", """
with_params: {uri: /num/test/fn, partial: true, params: {b: 2}}
bare: {uri: /num/test/fn, partial: true}
""")
    store = store_for([path])
    assert store.get("with_params")(3) == 5
    assert store.get("bare") is fn
    assert store.get("with_params") is store.get("with_params")


def test_build_error_wraps_cause(write):
    def boom(x):
        raise RuntimeError("nope")

    register("/num/test/boom", boom, description="fails")
    path = write("a.yaml", "bad: {uri: /num/test/boom, params: {x: 1}}\n")
    store = store_for([path])
    with pytest.raises(BuildError) as caught:
        store.get("bad")
    assert "bad" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_unknown_reference_at_build(write):
    path = write("a.yaml", "c: {uri: /a/b/c, partial: true}\n")
    store = store_for([path])
    with pytest.raises(BuildError):
        store.get("ghost")
