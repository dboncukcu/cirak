import pytest

from cirak import check, register, run
from cirak.api import analyze, flow_dump


def kinds(problems):
    return [problem.kind for problem in problems]


def errors_of(problems):
    return [problem.kind for problem in problems if problem.severity == "error"]


def expanded(paths):
    analysis = analyze(paths)
    assert [problem for problem in analysis.problems if problem.severity == "error"] == []
    return analysis.flow


def test_flow_block_opens_into_a_transparent_pipeline(write):
    register("/b/test/seed", lambda value: value, returns="seed")
    register("/b/test/scale", lambda value, factor, offset=0: value * factor + offset, returns="scaled")
    register("/b/test/report", lambda scaled: {"scaled": scaled}, returns="report")
    path = write("a.yaml", """
blocks:
  scaler:
    variables:
      factor: {required: true}
      seed: {default: 1}
    flow:
      seed: {uri: /b/test/seed, params: {value: $seed$}}
      scale: {uri: /b/test/scale, params: {factor: $factor$}, inputs: {value: seed}}
flow:
  outputs: [report]
  stage: {block: scaler, params: {factor: 3, seed: 2}}
  report: {uri: /b/test/report}
""")
    assert check([path]) == []
    flow = expanded([path])
    assert list(flow) == ["outputs", "stage", "report"]
    assert flow["stage"] == {"seed": {"uri": "/b/test/seed", "params": {"value": 2}},
                             "scale": {"uri": "/b/test/scale", "params": {"factor": 3}, "inputs": {"value": "seed"}}}
    report = run([path])
    assert report.outputs == {"report": {"scaled": 6}}
    assert report.tree["nodes"][0]["nodes"][1]["path"] == "stage.scale"


def test_usage_renames_at_a_closed_boundary(write):
    register("/b/test/const", lambda value: value)
    register("/b/test/double", lambda x: x * 2, returns="doubled")
    path = write("a.yaml", """
blocks:
  doubler:
    flow:
      double: {uri: /b/test/double}
flow:
  outputs: [twice_left, twice_right]
  left: {uri: /b/test/const, params: {value: 1}}
  right: {uri: /b/test/const, params: {value: 5}}
  a: {block: doubler, inputs: {left: x}, outputs: {doubled: twice_left}}
  b: {block: doubler, inputs: {right: x}, outputs: {doubled: twice_right}}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"twice_left": 2, "twice_right": 10}


def test_variables_carry_references_and_field_access(write):
    register("/b/test/apply", lambda fn, value: fn(value), returns="applied")
    register("/b/test/inc", lambda value, by: value + by, partial=True)
    register("/b/test/five", lambda: 5, returns="value")
    register("/b/test/named", lambda label: label, returns="label")
    path = write("a.yaml", """
alias:
  five: /b/test/five
blocks:
  stage:
    variables:
      fn: {required: true}
      source: {required: true}
    flow:
      value: {uri: $source.uri$, params: $source.params$}
      applied: {uri: /b/test/apply, params: {fn: $fn$}}
      label: {uri: /b/test/named, params: {label: tag_$source.tag$}}
bump: {uri: /b/test/inc, params: {by: 10}}
flow:
  outputs: [applied, label]
  stage: {block: stage, params: {fn: "@bump", source: {uri: five, params: {}, tag: x}}}
""")
    assert check([path]) == []
    flow = expanded([path])
    assert flow["stage"]["value"] == {"uri": "/b/test/five"}
    assert flow["stage"]["applied"]["params"] == {"fn": "@bump"}
    assert run([path]).outputs == {"applied": 15, "label": "tag_x"}


def test_missing_field_and_unknown_variable_are_reported(write):
    path = write("a.yaml", """
blocks:
  stage:
    variables:
      source: {required: true}
    flow:
      value: {uri: /a/b/c, params: $source.params$}
flow:
  stage: {block: stage, params: {source: {uri: /a/b/c}}}
""")
    problems = check([path])
    assert kinds(problems) == ["unknown_variable"]
    assert "params" in problems[0].message


def test_foreach_names_nodes_by_key_template_or_index(write):
    register("/b/test/frame", lambda set: f"{set}!", returns="frame")
    path = write("a.yaml", """
blocks:
  data:
    variables:
      sets: {default: [train, valid, test]}
    flow:
      frame:
        foreach: {over: $sets$, item: set, key: $set$,
                  node: {uri: /b/test/frame, params: {set: $set$}, outputs: [$set$_frame]}}
      plain:
        foreach: {over: $sets$, item: set,
                  node: {uri: /b/test/frame, params: {set: $set$}, outputs: [$set$_plain]}}
flow:
  outputs: [train_frame, test_frame]
  data: {block: data, params: {sets: [train, test]}}
""")
    assert check([path]) == []
    flow = expanded([path])
    assert list(flow["data"]) == ["frame_train", "frame_test", "plain_1", "plain_2"]
    assert flow["data"]["frame_train"] == {"uri": "/b/test/frame", "params": {"set": "train"},
                                           "outputs": ["train_frame"]}
    assert run([path]).outputs == {"train_frame": "train!", "test_frame": "test!"}


def test_foreach_chain_index_and_count(write):
    def open_rules(rules):
        return dict(rules)

    def rule(rules, name, value):
        return {**rules, name: value}

    register("/b/test/empty", lambda: {}, returns="rules")
    register("/b/test/open", open_rules)
    register("/b/test/rule", rule)
    register("/b/test/identity", lambda value: value, aliases="value")
    path = write("a.yaml", """
blocks:
  ruling:
    variables:
      rules: {default: []}
    flow:
      rules_0: {uri: /b/test/open, inputs: {rules: rules}}
      rule:
        foreach: {over: $rules$, index: i, count: n, chain: rules,
                  node: {uri: /b/test/rule, params: $item$}}
      rules_ruled: {uri: /b/test/identity, inputs: {value: rules_$n$}}
flow:
  outputs: [rules_ruled]
  rules: {uri: /b/test/empty}
  ruling: {block: ruling, params: {rules: [{name: a, value: 1}, {name: b, value: 2}]}}
""")
    assert check([path]) == []
    flow = expanded([path])
    assert list(flow["ruling"]) == ["rules_0", "rule_1", "rule_2", "rules_ruled"]
    assert flow["ruling"]["rule_1"] == {"uri": "/b/test/rule", "params": {"name": "a", "value": 1},
                                        "inputs": {"rules": "rules_0"}, "outputs": ["rules_1"]}
    assert flow["ruling"]["rule_2"]["inputs"] == {"rules": "rules_1"}
    assert flow["ruling"]["rules_ruled"]["inputs"] == {"value": "rules_2"}
    assert run([path]).outputs == {"rules_ruled": {"a": 1, "b": 2}}
    empty = write("b.yaml", """
blocks:
  ruling:
    variables:
      rules: {default: []}
    flow:
      rules_0: {uri: /b/test/open, inputs: {rules: rules}}
      rule:
        foreach: {over: $rules$, index: i, count: n, chain: rules,
                  node: {uri: /b/test/rule, params: $item$}}
      rules_ruled: {uri: /b/test/identity, inputs: {value: rules_$n$}}
flow:
  outputs: [rules_ruled]
  rules: {uri: /b/test/empty}
  ruling: {block: ruling}
""")
    flow = expanded([empty])
    assert list(flow["ruling"]) == ["rules_0", "rules_ruled"]
    assert flow["ruling"]["rules_ruled"]["inputs"] == {"value": "rules_0"}
    assert run([empty]).outputs == {"rules_ruled": {}}


def test_foreach_over_mapping_names_nodes_by_the_mapping_key(write):
    register("/b/test/opt", lambda lr, model: (model, lr))
    register("/b/test/pack", lambda items: items, returns="optimizers")
    register("/b/test/model", lambda: "m")
    path = write("a.yaml", """
blocks:
  optimizers:
    variables:
      table: {required: true}
    flow:
      build:
        foreach: {over: $table$, item: o,
                  node: {uri: /b/test/opt, params: {lr: $o.lr$}, inputs: {model: $o.model$}, outputs: [opt_$o.name$]}}
      optimizers: {uri: /b/test/pack, inputs: {items: {g: opt_g, d: opt_d}}}
flow:
  outputs: [optimizers]
  net: {uri: /b/test/model}
  opt: {block: optimizers, params: {table: {g: {lr: 0.1, model: net, name: g}, d: {lr: 0.2, model: net, name: d}}}}
""")
    assert check([path]) == []
    flow = expanded([path])
    assert list(flow["opt"]) == ["build_g", "build_d", "optimizers"]
    assert flow["opt"]["build_d"] == {"uri": "/b/test/opt", "params": {"lr": 0.2}, "inputs": {"model": "net"},
                                      "outputs": ["opt_d"]}
    assert run([path]).outputs == {"optimizers": {"g": ("m", 0.1), "d": ("m", 0.2)}}


def test_foreach_key_template_reads_item_fields(write):
    register("/b/test/build", lambda seed, index: (seed, index))
    path = write("a.yaml", """
blocks:
  models:
    variables:
      items: {required: true}
    flow:
      build:
        foreach: {over: $items$, item: m, key: $m.name$,
                  node: {uri: /b/test/build, params: {seed: 7, index: $m.index$}, outputs: [$m.name$]}}
flow:
  outputs: [encoder, head]
  models: {block: models, params: {items: [{name: encoder, index: 0}, {name: head, index: 1}]}}
""")
    assert check([path]) == []
    assert list(expanded([path])["models"]) == ["build_encoder", "build_head"]
    assert run([path]).outputs == {"encoder": (7, 0), "head": (7, 1)}


def test_foreach_inside_a_loop_body_of_a_block(write):
    register("/b/test/zero", lambda: 0, returns="n")
    register("/b/test/two", lambda: 2, returns="turns")
    register("/b/test/bump", lambda n: n + 1, returns="n_next")
    register("/b/test/score", lambda n_next, set: f"{set}:{n_next}")
    register("/b/test/merge", lambda parts: dict(parts), returns="metrics")
    path = write("a.yaml", """
blocks:
  training:
    variables:
      sets: {default: [valid, test]}
    flow:
      epochs:
        loop:
          carry: [n]
          next: _next
          range: turns
          trace: {metrics: history}
          body:
            bump: {uri: /b/test/bump}
            evaluate:
              foreach: {over: $sets$, item: set, key: $set$,
                        node: {uri: /b/test/score, params: {set: $set$}, outputs: [$set$_metrics]}}
            metrics: {uri: /b/test/merge, inputs: {parts: "*_metrics"}}
flow:
  outputs: [history]
  n: {uri: /b/test/zero}
  turns: {uri: /b/test/two}
  training: {block: training}
""")
    assert check([path]) == []
    body = expanded([path])["training"]["epochs"]["loop"]["body"]
    assert list(body) == ["bump", "evaluate_valid", "evaluate_test", "metrics"]
    assert run([path]).outputs == {"history": [{"valid_metrics": "valid:1", "test_metrics": "test:1"},
                                               {"valid_metrics": "valid:2", "test_metrics": "test:2"}]}


def test_foreach_in_the_root_flow(write):
    register("/b/test/const", lambda value: value)
    path = write("a.yaml", """
params:
  names: [a, b]
flow:
  outputs: [a, b]
  make:
    foreach: {over: $names$, item: name, node: {uri: /b/test/const, params: {value: $name$}, outputs: [$name$]}}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"a": "a", "b": "b"}


def test_foreach_limits(write):
    base = """
blocks:
  stage:
    variables:
      items: {default: [x, y]}
    flow:
%s
flow:
  stage: {block: stage}
"""
    nested = write("nested.yaml", base % """
      outer:
        foreach: {over: $items$, node: {inner: {foreach: {over: $items$, node: {uri: /a/b/c}}}}}""")
    assert errors_of(check([nested])) == ["invalid_foreach"]
    chained = write("chained.yaml", base % """
      outer:
        foreach: {over: $items$, chain: k, node: {uri: /a/b/c, inputs: {k: k_0}}}""")
    assert errors_of(check([chained])) == ["invalid_foreach"]
    scalar = write("scalar.yaml", base % """
      outer:
        foreach: {over: 5, node: {uri: /a/b/c}}""")
    assert errors_of(check([scalar])) == ["invalid_foreach"]
    unknown = write("unknown.yaml", base % """
      outer:
        foreach: {over: $items$, filter: yes, node: {uri: /a/b/c}}""")
    assert errors_of(check([unknown])) == ["invalid_foreach"]
    keyed = write("keyed.yaml", base % """
      outer:
        foreach: {over: $items$, key: [$item$], node: {uri: /a/b/c}}""")
    assert errors_of(check([keyed])) == ["invalid_foreach"]
    clash = write("clash.yaml", base % """
      outer:
        foreach: {over: $items$, count: items, node: {uri: /a/b/c}}""")
    assert errors_of(check([clash])) == ["invalid_foreach"]
    field = write("field.yaml", base % """
      outer:
        foreach: {over: $items$, item: m, node: {uri: /a/b/c, params: {v: $m.name$}}}""")
    assert errors_of(check([field])) == ["unknown_variable"]


def test_block_kind_mismatches(write):
    path = write("a.yaml", """
blocks:
  graphy:
    inputs: [x]
    outputs: [y]
    graph:
      y: {uri: /a/b/c, partial: true, inputs: x}
  flowy:
    flow:
      step: {uri: /a/b/c, outputs: [z]}
  both:
    flow: {}
    spec: []
thing: {block: flowy, builder: /b/p/x}
flow:
  stage: {block: graphy}
  loop_me: {block: flowy, block2: 1}
""")
    found = kinds(check([path]))
    assert found.count("invalid_block") == 4


def test_flow_block_cycle(write):
    path = write("a.yaml", """
blocks:
  a:
    flow:
      inner: {block: b}
  b:
    flow:
      inner: {block: a}
flow:
  stage: {block: a}
""")
    assert "block_cycle" in kinds(check([path]))


def test_problems_in_expanded_nodes_point_at_the_template(write):
    register("/b/test/needs", lambda value, other: value)
    path = write("a.yaml", """
blocks:
  stage:
    flow:
      seed: {uri: /a/b/c, outputs: [seed]}
      bad: {uri: /b/test/needs, params: {nope: 1}, inputs: {value: seed}, outputs: []}
flow:
  stage: {block: stage}
""")
    problems = [problem for problem in check([path]) if problem.kind == "signature_mismatch"]
    assert len(problems) == 2
    assert all(problem.line == 6 for problem in problems)
    assert "flow.stage.bad" in problems[0].message


def test_flow_dump_shows_expanded_nodes_with_resolution_comments(write, tmp_path):
    register("/b/test/frame", lambda set, device="cpu": f"{set}@{device}", bus=["device"])
    register("/b/test/merge", lambda parts: dict(parts), returns="metrics")
    path = write("a.yaml", """
blocks:
  data:
    variables:
      sets: {default: [train, test]}
    flow:
      frame:
        foreach: {over: $sets$, item: set, key: $set$,
                  node: {uri: /b/test/frame, params: {set: $set$}, outputs: [$set$_metrics]}}
      metrics: {uri: /b/test/merge, inputs: {parts: "*_metrics"}}
flow:
  outputs: [metrics]
  data: {block: data}
""")
    text = flow_dump([path], inputs=["device"])
    assert "foreach" not in text
    assert "frame_train:" in text and "frame_test:" in text
    assert "# implicit: device" in text
    assert "# parts *_metrics: test_metrics, train_metrics" in text
    assert "data:" in text and "# reads: device; exports: metrics" in text
    record = tmp_path / "rec"
    run([path], inputs={"device": "cuda"}, record_dir=str(record))
    assert (record / "flow.yaml").read_text() == text
    assert "blocks:" in (record / "resolved.yaml").read_text()


def test_unused_variable_counts_flow_usage(write):
    path = write("a.yaml", """
blocks:
  stage:
    variables:
      used: {default: 1}
      spare: {default: 2}
    flow:
      step: {uri: /a/b/c, params: {v: $used$}, outputs: []}
flow:
  stage: {block: stage}
""")
    unused = [problem for problem in check([path]) if problem.kind == "unused_variable"]
    assert [problem.message for problem in unused] == ["variable 'spare' of block 'stage' is never used"]
