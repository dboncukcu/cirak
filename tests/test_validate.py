from cirak.api import check


def kinds(problems):
    return sorted(problem.kind for problem in problems)


def errors_of(problems):
    return sorted(problem.kind for problem in problems if problem.severity == "error")


def test_component_matrix(write):
    path = write("a.yaml", """
both: {uri: /a/b/c, block: x}
partial_block: {block: chain, partial: true, builder: /b/p/x}
builder_uri: {uri: /a/b/c, builder: /b/p/x}
blocks:
  chain:
    spec:
      - {uri: /a/b/c, partial: true}
""")
    found = errors_of(check([path]))
    assert found.count("invalid_component") == 3
    assert "unknown_block" in found


def test_missing_builder(write):
    path = write("a.yaml", """
blocks:
  chain:
    spec:
      - {uri: /a/b/c, partial: true}
c:
  block: chain
""")
    assert "missing_builder" in errors_of(check([path]))


def test_builder_from_block_definition(write):
    path = write("a.yaml", """
blocks:
  chain:
    builder: /builder/proj/compose
    spec:
      - {uri: /a/b/c, partial: true}
c:
  block: chain
""")
    assert "missing_builder" not in kinds(check([path]))


def test_graph_rules(write):
    path = write("a.yaml", """
blocks:
  bad:
    inputs: [x]
    outputs: [out, ghost]
    graph:
      a: {uri: /a/b/c, partial: true, inputs: nothing}
      b: {uri: /a/b/c, partial: true, inputs: x, outputs: [dup]}
      c: {uri: /a/b/c, partial: true, inputs: x, outputs: [dup]}
      out: {uri: /a/b/c, partial: true, inputs: a}
      lonely: {uri: /a/b/c, partial: true, inputs: x}
comp:
  block: bad
  builder: /b/p/x
""")
    found = kinds(check([path]))
    assert "unproduced_input" in found
    assert "double_assignment" in found
    assert "missing_output" in found
    assert "unused_output" in found


def test_graph_cycle(write):
    path = write("a.yaml", """
blocks:
  loopy:
    inputs: [x]
    outputs: [b]
    graph:
      a: {uri: /a/b/c, partial: true, inputs: b}
      b: {uri: /a/b/c, partial: true, inputs: a}
comp:
  block: loopy
  builder: /b/p/x
""")
    assert "graph_cycle" in kinds(check([path]))


def test_shadowed_and_unused_variable(write):
    path = write("a.yaml", """
params:
  window: 5
blocks:
  chain:
    variables:
      window: {default: 3}
      spare: {default: 1}
    spec:
      - {uri: /a/b/c, partial: true, params: {w: $window$}}
comp:
  block: chain
  builder: /b/p/x
""")
    found = kinds(check([path]))
    assert "shadowed_variable" in found
    assert "unused_variable" in found


def test_references(write):
    path = write("a.yaml", """
conditions:
  above: {uri: /c/p/above, partial: true, params: {limit: 10}}
helper: {uri: /a/b/c, partial: true, params: {fn: "@conditions.above"}}
ghost_user: {uri: /a/b/c, partial: true, params: {fn: "@missing"}}
flow:
  work: {uri: /a/b/c, params: {check: "@helper", all: "@conditions"}, outputs: [done]}
""")
    problems = check([path])
    assert "unknown_reference" in errors_of(problems)
    unused = [problem for problem in problems if problem.kind == "unused_component"]
    assert [problem.message for problem in unused] == ["component ghost_user is never referenced"]


def test_reference_cycle(write):
    path = write("a.yaml", """
a: {uri: /a/b/c, partial: true, params: {other: "@b"}}
b: {uri: /a/b/c, partial: true, params: {other: "@a"}}
""")
    assert "reference_cycle" in kinds(check([path]))


def test_reference_in_block_params(write):
    path = write("a.yaml", """
blocks:
  chain:
    variables:
      fn: {required: true}
    spec:
      - {uri: /a/b/c, partial: true, params: {f: $fn$}}
other: {uri: /a/b/c, partial: true}
comp:
  block: chain
  builder: /b/p/x
  params: {fn: "@other"}
""")
    assert "forbidden_placeholder" in errors_of(check([path]))


def test_flow_nodes(write):
    path = write("a.yaml", """
flow:
  broken: {params: {x: 1}, outputs: [y]}
  pick:
    branch:
      decide: /c/p/d
      cases:
        "true": {uri: /a/b/c, inputs: [y], outputs: [t]}
        other: {uri: /a/b/c, inputs: [y], outputs: [t]}
""")
    found = kinds(check([path]))
    assert "ambiguous_node" in found
    assert "quoted_bool_label" in found


def test_reserved_name(write):
    path = write("a.yaml", """
flow:
  stage:
    inputs: {uri: /a/b/c}
    real: {uri: /a/b/c, outputs: [x]}
""")
    assert "reserved_name" in kinds(check([path]))
