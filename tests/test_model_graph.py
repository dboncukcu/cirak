import pytest

from cirak import BuildError, check, register
from cirak.api import analyze
from cirak.build import build_components


def kinds(problems):
    return [problem.kind for problem in problems]


def errors_of(problems):
    return [problem.kind for problem in problems if problem.severity == "error"]


def expansions_of(paths):
    analysis = analyze(paths)
    return analysis.expansions, analysis.problems


TIDY_BLOCKS = """
blocks:
  mlp_hidden:
    variables: {width: {required: true}}
    spec:
      - {uri: /layer/test/linear, params: {in_features: $width$, out_features: $width$}}
      - {uri: /layer/test/relu}
  mlp:
    variables: {in_features: {required: true}, out_features: {required: true}, width: {default: 64}, depth: {default: 2}}
    inputs: [x]
    outputs: [y]
    graph:
      h0: {uri: /layer/test/linear, params: {in_features: $in_features$, out_features: $width$}, inputs: [x]}
      a0: {uri: /layer/test/relu, inputs: [h0]}
      h:  {block: mlp_hidden, params: {width: $width$}, repeat: $depth$, inputs: [a0]}
      j:  {uri: /layer/test/concat, params: {dim: 1}, inputs: [x, h]}
      y:  {uri: /layer/test/linear, params: {out_features: $out_features$}, inputs: [j]}
  branch:
    variables: {in_features: {required: true}, width: {required: true}}
    spec:
      - {uri: /layer/test/linear, params: {in_features: $in_features$, out_features: $width$}}
      - {uri: /layer/test/relu}
  head:
    variables: {in_features: {required: true}, width: {required: true}}
    inputs: [c]
    outputs: [feature, logit]
    graph:
      h:       {uri: /layer/test/linear, params: {in_features: $in_features$, out_features: $width$}, inputs: [c]}
      feature: {uri: /layer/test/relu, inputs: [h]}
      logit:   {uri: /layer/test/linear, params: {in_features: $width$, out_features: 1}, inputs: [feature]}
  encoder:
    inputs: [x]
    outputs: [z]
    spec:
      - {block: mlp, params: {in_features: 6, out_features: 64, width: 64, depth: 2}}
      - {uri: /layer/test/relu}
      - {uri: /layer/test/linear, params: {in_features: 64, out_features: 4}, init: {std: 0.02}}
  dxz:
    inputs: [x, z]
    outputs: [logit]
    graph:
      xb:   {block: branch, params: {in_features: 6, width: 32}, inputs: [x]}
      zb:   {block: branch, params: {in_features: 4, width: 32}, inputs: [z]}
      j:    {uri: /layer/test/concat, params: {dim: 1}, inputs: [xb, zb]}
      head: {block: head, params: {in_features: 64, width: 64}, inputs: [j], outputs: [feature, logit]}
  dxx:
    inputs: [x, x_hat]
    outputs: [logit, feature]
    graph:
      j:    {uri: /layer/test/concat, params: {dim: 1}, inputs: [x, x_hat]}
      head: {block: head, params: {in_features: 12, width: 64}, inputs: [j], outputs: [feature, logit]}
  anomaly_score:
    inputs: [x]
    outputs: [score]
    graph:
      z:     {model: encoder, inputs: [x]}
      x_hat: {model: generator, inputs: [z]}
      rec:   {model: dxx, inputs: [x, x_hat], outputs: [logit_rec, feature_rec]}
      ref:   {model: dxx, inputs: [x, x], outputs: [logit_ref, feature_ref]}
      score: {uri: /layer/test/l1, inputs: [feature_rec, feature_ref]}
"""

USES = """
enc: {block: encoder, builder: /builder/test/names}
dxz_m: {block: dxz, builder: /builder/test/names}
dxx_m: {block: dxx, builder: /builder/test/names}
score: {block: anomaly_score, builder: /builder/test/names}
"""


def layers():
    register("/layer/test/linear", lambda in_features=None, out_features=None: ("linear", in_features, out_features),
         kind="layer")
    register("/layer/test/relu", lambda: "relu", kind="layer")
    register("/layer/test/concat", lambda dim: ("concat", dim), kind="layer")
    register("/layer/test/l1", lambda: "l1", kind="layer")
    register("/builder/test/names", lambda graph: graph, kind="builder")


def test_tidy_blocks_expand_like_the_dump(write):
    layers()
    path = write("a.yaml", TIDY_BLOCKS + USES)
    expansions, problems = expansions_of([path])
    assert errors_of(problems) == []
    encoder = expansions["enc"]
    assert encoder["inputs"] == ["x"] and encoder["outputs"] == ["z"]
    order = list(encoder["graph"])
    assert order == ["s0.h0", "s0.a0", "s0.h_0.s0", "s0.h_0.s1", "s0.h_1.s0", "s0.h_1.s1", "s0.j", "s0.y",
                     "s1", "s2"]
    assert encoder["graph"]["s0.h0"]["inputs"] == ["x"]
    assert encoder["graph"]["s0.h_0.s0"]["inputs"] == ["s0.a0"]
    assert encoder["graph"]["s0.h_1.s0"]["inputs"] == ["s0.h_0"]
    assert encoder["graph"]["s0.h_1.s1"]["outputs"] == ["s0.h"]
    assert encoder["graph"]["s0.j"]["inputs"] == ["x", "s0.h"]
    assert encoder["graph"]["s2"]["outputs"] == ["z"]
    assert encoder["graph"]["s2"]["init"] == {"std": 0.02}
    dxz = expansions["dxz_m"]
    assert dxz["graph"]["head.logit"]["outputs"] == ["logit"]
    assert dxz["graph"]["head.feature"]["outputs"] == ["feature"]
    assert dxz["graph"]["head.h"]["inputs"] == ["j"]
    warnings = [problem for problem in problems if problem.kind == "unused_output"]
    assert any("'feature'" in problem.message and "dxz_m" in problem.message for problem in warnings)
    dxx = expansions["dxx_m"]
    assert dxx["outputs"] == ["logit", "feature"]
    assert "unused_output" not in [problem.kind for problem in problems if "dxx_m" in problem.message]
    score = expansions["score"]
    assert score["graph"]["z"] == {"ref": "encoder", "inputs": ["x"], "outputs": ["z"], "unpack": False}
    assert score["graph"]["rec"] == {"ref": "dxx", "inputs": ["x", "x_hat"], "outputs": ["logit_rec", "feature_rec"],
                                     "unpack": True}
    assert score["graph"]["score"]["inputs"] == ["feature_rec", "feature_ref"]


def test_builder_receives_reference_nodes_and_extra(write):
    layers()
    path = write("a.yaml", TIDY_BLOCKS + USES)
    analysis = analyze([path])
    store = build_components(analysis.data, analysis.expansions)
    graph = store.get("score")
    by_name = {node.name: node for node in graph.nodes}
    assert by_name["z"].ref == "encoder" and by_name["z"].obj is None
    assert by_name["rec"].unpack is True and by_name["rec"].outputs == ("logit_rec", "feature_rec")
    assert by_name["score"].obj == "l1" and by_name["score"].ref is None
    encoder = store.get("enc")
    last = {node.name: node for node in encoder.nodes}["s2"]
    assert last.extra == {"init": {"std": 0.02}}
    assert last.obj == ("linear", 64, 4)


def test_compose_refuses_reference_nodes(write):
    layers()
    path = write("a.yaml", TIDY_BLOCKS + "score: {block: anomaly_score, builder: /builder/cirak/compose}\n")
    analysis = analyze([path])
    store = build_components(analysis.data, analysis.expansions)
    with pytest.raises(BuildError, match="references 'encoder'"):
        store.get("score")


def test_repeat_on_a_uri_node_and_zero_repeat_aliases_the_wire(write):
    layers()
    path = write("a.yaml", """
blocks:
  deep:
    variables: {depth: {required: true}}
    inputs: [x]
    outputs: [y]
    graph:
      h: {uri: /layer/test/relu, inputs: [x], repeat: $depth$}
      y: {uri: /layer/test/linear, params: {out_features: 1}, inputs: [h]}
three: {block: deep, params: {depth: 3}, builder: /builder/test/names}
none: {block: deep, params: {depth: 0}, builder: /builder/test/names}
""")
    expansions, problems = expansions_of([path])
    assert errors_of(problems) == []
    three = expansions["three"]["graph"]
    assert list(three) == ["h_0", "h_1", "h_2", "y"]
    assert three["h_1"]["inputs"] == ["h_0"] and three["h_2"]["outputs"] == ["h"]
    none = expansions["none"]["graph"]
    assert list(none) == ["y"]
    assert none["y"]["inputs"] == ["x"]


def test_zero_repeat_on_the_output_node_aliases_the_block_output(write):
    layers()
    path = write("a.yaml", """
blocks:
  maybe:
    inputs: [x]
    outputs: [y]
    graph:
      y: {uri: /layer/test/relu, inputs: [x], repeat: 0}
skip: {block: maybe, builder: /builder/test/names}
""")
    expansions, problems = expansions_of([path])
    assert errors_of(problems) == []
    assert expansions["skip"] == {"inputs": ["x"], "outputs": ["x"], "graph": {}, "declared": {}}


def test_graph_extension_errors(write):
    layers()
    path = write("a.yaml", """
blocks:
  two_in:
    inputs: [a, b]
    outputs: [y]
    graph:
      y: {uri: /layer/test/concat, params: {dim: 1}, inputs: [a, b], repeat: 2}
  wrong_name:
    inputs: [x]
    outputs: [y]
    graph:
      y: {block: pair, inputs: [x], outputs: [nope]}
  pair:
    inputs: [c]
    outputs: [feature, logit]
    graph:
      feature: {uri: /layer/test/relu, inputs: [c]}
      logit: {uri: /layer/test/relu, inputs: [c]}
  unknown_key:
    inputs: [x]
    outputs: [y]
    graph:
      y: {uri: /layer/test/relu, inputs: [x], colour: red}
  model_params:
    inputs: [x]
    outputs: [y]
    graph:
      y: {model: enc, inputs: [x], params: {a: 1}}
  two_names:
    inputs: [x, z]
    outputs: [y]
    spec:
      - {uri: /layer/test/relu}
  count_mismatch:
    inputs: [x]
    outputs: [y]
    graph:
      y: {block: pair, inputs: [x]}
a: {block: two_in, builder: /builder/test/names}
b: {block: wrong_name, builder: /builder/test/names}
c: {block: unknown_key, builder: /builder/test/names}
d: {block: model_params, builder: /builder/test/names}
e: {block: two_names, builder: /builder/test/names}
f: {block: count_mismatch, builder: /builder/test/names}
""")
    found = errors_of(check([path]))
    assert "bad_repeat" in found
    assert "unknown_output" in found
    assert "unknown_key" in found
    assert found.count("invalid_block") == 2
    assert "spec_arity" in found


def test_named_spec_boundaries_with_a_single_node(write):
    layers()
    path = write("a.yaml", """
blocks:
  one:
    inputs: [x]
    outputs: [z]
    spec:
      - {uri: /layer/test/relu}
c: {block: one, builder: /builder/test/names}
""")
    expansions, problems = expansions_of([path])
    assert errors_of(problems) == []
    assert expansions["c"] == {"inputs": ["x"], "outputs": ["z"],
                               "graph": {"s0": {"uri": "/layer/test/relu", "params": {}, "partial": False,
                                                "unpack": False, "inputs": ["x"], "outputs": ["z"]}},
                               "declared": {}}
