from cirak import check, lego, run


def kinds(problems):
    return [problem.kind for problem in problems]


def test_loop_grammar_range_key_next_suffix_until_key_transparent_body(write):
    def seed():
        return {"w": 1.0}

    def epochs_left():
        return 5

    def turn(models, loader, turn_index, device="cpu"):
        models["w"] += loader * (turn_index + 1)
        return {"models": models, "metrics": {"w": models["w"], "device": device}}

    def judge(metrics):
        return {"stop": metrics["w"] >= 7.0}

    lego("/g/test/seed", seed, returns="models")
    lego("/g/test/left", epochs_left, returns="epochs_left")
    lego("/g/test/loader", lambda: 1.0, returns="train_loader")
    lego("/g/test/turn", turn, returns=["models", "metrics"], mutates=["models"], bus=["device"])
    lego("/g/test/judge", judge, returns=["stop"])
    path = write("a.yaml", """
flow:
  outputs: [history, models]
  seed: {uri: /g/test/seed}
  left: {uri: /g/test/left}
  loader: {uri: /g/test/loader}
  epochs:
    loop:
      carry: [models]
      next: _next
      range: epochs_left
      index: turn_index
      until: stop
      trace: {metrics: history}
      outputs: {models: models_final}
      body:
        turn: {uri: /g/test/turn, inputs: {models: models, loader: train_loader, turn_index: turn_index},
               outputs: {models: models_next, metrics: metrics}}
        judge: {uri: /g/test/judge, inputs: [metrics]}
""")
    problems = check([path], inputs=["device"])
    assert problems == []
    report = run([path], inputs={"device": "cuda"})
    assert [entry["w"] for entry in report.outputs["history"]] == [2.0, 4.0, 7.0]
    assert report.outputs["history"][0]["device"] == "cuda"
    assert report.outputs["models"] == {"w": 7.0}


def test_input_binding_forms_pattern_group_and_list(write):
    def one():
        return 1

    def merge(parts):
        return {key: value for key, value in parts.items()}

    def pack(items):
        return items

    def total(values):
        return sum(values)

    lego("/g/test/one", one)
    lego("/g/test/merge", merge, returns="metrics")
    lego("/g/test/pack", pack)
    lego("/g/test/total", total)
    path = write("a.yaml", """
flow:
  outputs: [metrics, pack, total]
  a_metrics: {uri: /g/test/one}
  b_metrics: {uri: /g/test/one}
  merge: {uri: /g/test/merge, inputs: {parts: "*_metrics"}}
  pack: {uri: /g/test/pack, inputs: {items: {left: a_metrics, right: b_metrics}}}
  total: {uri: /g/test/total, inputs: {values: [a_metrics, b_metrics]}}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"metrics": {"a_metrics": 1, "b_metrics": 1},
                                   "pack": {"left": 1, "right": 1}, "total": 2}


def test_map_collect_list_form_and_index(write):
    lego("/g/test/items", lambda: [3, 1, 2], returns="xs")
    lego("/g/test/scale", lambda x, i: x * 10 + i, returns="y")
    path = write("a.yaml", """
flow:
  outputs: [y]
  feed: {uri: /g/test/items}
  fan:
    map:
      over: xs
      item: x
      index: i
      collect: [y]
      body: {uri: /g/test/scale}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"y": [30, 11, 22]}


def test_loop_and_map_shape_problems(write):
    path = write("a.yaml", """
flow:
  seed: {uri: /a/b/c, outputs: [x, xs], unpack: true}
  no_range:
    loop:
      carry: [x]
      body: {uri: /a/b/c, inputs: [x], outputs: [x]}
  bad_shapes:
    loop:
      carry: []
      range: 2.5
      next: 3
      trace: [x]
      until: /not/a/key
      body: {uri: /a/b/c, inputs: [x], outputs: [x]}
  bad_map:
    map:
      over: [xs]
      collect: 3
      parallel: many
      body: {uri: /a/b/c, inputs: [item], outputs: [y]}
  no_body:
    map:
      over: xs
""")
    found = kinds(check([path]))
    assert found.count("invalid_loop") == 5
    assert "invalid_condition" in found
    assert found.count("invalid_map") == 3
    assert "ambiguous_node" in found


def test_until_predicate_mapping_form(write):
    lego("/g/test/bump", lambda x: x + 1, returns="x_next")
    lego("/g/test/big", lambda x_next, limit: x_next >= limit, kind="predicate")
    path = write("a.yaml", """
flow:
  outputs: [final]
  seed: {uri: /a/b/c, outputs: [seed]}
  climb:
    loop:
      carry: {x: seed}
      next: {x: x_next}
      range: 10
      until: {uri: /g/test/big, params: {limit: 3}}
      outputs: {x: final}
      body: {uri: /g/test/bump}
""")
    assert check([path]) == []
    lego("/g/test/zero", lambda: 0)
    path = write("b.yaml", """
flow:
  outputs: [final]
  seed: {uri: /g/test/zero}
  climb:
    loop:
      carry: {x: seed}
      next: {x: x_next}
      range: 10
      until: {uri: /g/test/big, params: {limit: 3}}
      outputs: {x: final}
      body: {uri: /g/test/bump}
""")
    assert run([path]).outputs == {"final": 3}
