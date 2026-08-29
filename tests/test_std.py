from cirak import register
from cirak.api import analyze
from cirak.build import build_components


def build(paths):
    analysis = analyze(paths)
    assert [problem for problem in analysis.problems if problem.severity == "error"] == []
    return build_components(analysis.data, analysis.expansions)


def test_std_compose_chains_spec_blocks(write):
    def inc(value, step):
        return value + step

    register("/num/std/inc", inc, description="d")
    path = write("a.yaml", """
blocks:
  chain:
    variables:
      step: {required: true}
    spec:
      - {uri: /num/std/inc, partial: true, params: {step: $step$}, repeat: 3}
adder:
  block: chain
  params: {step: 5}
  builder: /builder/cirak/compose
""")
    assert build([path]).get("adder")(1) == 16


def test_std_compose_returns_tuple_for_multi_output(write):
    def divide(value, by):
        return value // by, value % by

    register("/num/std/divide", divide, description="d")
    path = write("a.yaml", """
blocks:
  divider:
    inputs: [value]
    outputs: [q, r]
    graph:
      split: {uri: /num/std/divide, partial: true, params: {by: 4}, inputs: value, outputs: [q, r]}
divmod4:
  block: divider
  builder: /builder/cirak/compose
""")
    assert build([path]).get("divmod4")(11) == (2, 3)


def test_std_catalog_visible_after_import():
    from cirak.registry import registry
    entry = registry.lookup("/builder/cirak/compose")
    assert entry is not None
    assert "callable" in entry.description
