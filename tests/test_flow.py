import pytest

from cirak import ConfigError, check, register, run


def test_flow_runs_on_tezgah(write):
    seen = {}

    def make_data(count):
        return list(range(count))

    def total(numbers, offset):
        return sum(numbers) + offset

    def sink(result):
        seen["result"] = result

    register("/num/flow/make", make_data, description="d")
    register("/num/flow/total", total, description="d")
    register("/num/flow/sink", sink, description="d")
    path = write("a.yaml", """
params:
  count: 4
flow:
  outputs: [grand]
  make: {uri: /num/flow/make, params: {count: $count$}, outputs: [numbers]}
  total: {uri: /num/flow/total, params: {offset: 10}, inputs: [numbers], outputs: [grand]}
  log: {uri: /num/flow/sink, inputs: {result: grand}, outputs: []}
""")
    report = run([path])
    assert report.outputs == {"grand": 16}
    assert seen["result"] == 16


def test_component_injection(write):
    def scale(value, factor):
        return value * factor

    def apply_fn(fn, x):
        return fn(x)

    def seven():
        return 7

    register("/num/flow/scale", scale, description="d")
    register("/num/flow/apply", apply_fn, description="d")
    register("/num/flow/seven", seven, description="d")
    path = write("a.yaml", """
doubler: {uri: /num/flow/scale, partial: true, params: {factor: 2}}
flow:
  outputs: [answer]
  seed: {uri: /num/flow/seven, outputs: [x]}
  apply: {uri: /num/flow/apply, params: {fn: "@doubler"}, inputs: [x], outputs: [answer]}
""")
    assert run([path]).outputs == {"answer": 14}


BRANCH_RECIPE = """
params:
  start: {start}
flow:
  outputs: [next]
  seed: {{uri: /num/flow/const, params: {{value: $start$}}, outputs: [n]}}
  pick:
    branch:
      decide: /num/flow/is_even
      cases:
        true: {{uri: /num/flow/halve, inputs: [n], outputs: [next]}}
        false: {{uri: /num/flow/triple, inputs: [n], outputs: [next]}}
"""


def test_branch_bool_labels_run_on_tezgah(write):
    def const(value):
        return value

    def is_even(n):
        return n % 2 == 0

    def halve(n):
        return n // 2

    def triple(n):
        return 3 * n + 1

    register("/num/flow/const", const, description="d")
    register("/num/flow/is_even", is_even, description="d")
    register("/num/flow/halve", halve, description="d")
    register("/num/flow/triple", triple, description="d")
    even = write("even.yaml", BRANCH_RECIPE.format(start=10))
    odd = write("odd.yaml", BRANCH_RECIPE.format(start=7))
    assert check([even]) == []
    assert run([even]).outputs == {"next": 5}
    assert run([odd]).outputs == {"next": 22}


def test_map_collects_in_input_order(write):
    def items():
        return [3, 1, 2]

    def double(item):
        return item * 2

    register("/num/flow/items", items, description="d")
    register("/num/flow/double", double, description="d")
    path = write("a.yaml", """
flow:
  outputs: [doubled]
  feed: {uri: /num/flow/items, outputs: [xs]}
  fan:
    map:
      over: xs
      item: item
      collect: doubled
      parallel: 2
      body: {uri: /num/flow/double, inputs: [item], outputs: [y]}
""")
    report = run([path], executor="thread", workers=2)
    assert report.outputs == {"doubled": [6, 2, 4]}


def test_loop_with_pipeline_body(write):
    def const(value):
        return value

    def bump(x):
        return x + 1

    def big(x):
        return x >= 3

    register("/num/flow/const2", const, description="d")
    register("/num/flow/bump", bump, description="d")
    register("/num/flow/big", big, description="d")
    path = write("a.yaml", """
flow:
  outputs: [final, history]
  seed: {uri: /num/flow/const2, params: {value: 0}, outputs: [seed]}
  climb:
    loop:
      carry: {x: seed}
      until: /num/flow/big
      max_iter: 10
      trace: {x: history}
      outputs: {x: final}
      body:
        inputs: [x]
        outputs: {x_next: x}
        up: {uri: /num/flow/bump, inputs: [x], outputs: [x_next]}
""")
    report = run([path])
    assert report.outputs == {"final": 3, "history": [1, 2, 3]}


def test_when_skips_side_effect(write):
    calls = []

    def const(value):
        return value

    def over_five(n):
        return n > 5

    def shout(n):
        calls.append(n)

    register("/num/flow/const3", const, description="d")
    register("/num/flow/over_five", over_five, description="d")
    register("/num/flow/shout", shout, description="d")
    path = write("a.yaml", """
flow:
  seed: {uri: /num/flow/const3, params: {value: 1}, outputs: [n]}
  alarm:
    uri: /num/flow/shout
    inputs: [n]
    outputs: []
    when: /num/flow/over_five
""")
    run([path])
    assert calls == []


def test_tezgah_validation_surfaces_as_config_error(write):
    def consume(missing_key):
        return missing_key

    register("/num/flow/consume", consume, description="d")
    path = write("a.yaml",
                 "flow:\n  outputs: [out2]\n"
                 "  bad: {uri: /num/flow/consume, inputs: [missing_key], outputs: [out2]}\n")
    with pytest.raises(ConfigError) as caught:
        run([path])
    found = [problem for problem in caught.value.problems if problem.kind == "tezgah_validation"]
    assert found
    assert found[0].file is not None


def test_run_writes_resolved_and_rerun_matches(write, tmp_path):
    def const(value):
        return value

    def add(a, b):
        return a + b

    register("/num/flow/const4", const, description="d")
    register("/num/flow/add", add, description="d")
    path = write("a.yaml", """
params:
  left: 2
  right: 3
flow:
  outputs: [total]
  one: {uri: /num/flow/const4, params: {value: $left$}, outputs: [a]}
  two: {uri: /num/flow/const4, params: {value: $right$}, outputs: [b]}
  sum: {uri: /num/flow/add, inputs: [a, b], outputs: [total]}
""")
    record = tmp_path / "runs" / "x1"
    report = run([path], record_dir=str(record))
    assert report.outputs == {"total": 5}
    assert (record / "resolved.yaml").exists()
    assert (record / "run.json").exists()
    rerun = run([str(record / "resolved.yaml")])
    assert rerun.outputs == report.outputs
