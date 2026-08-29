import pytest

from cirak import CirakWarning, ConfigError, check, resolve

FULL = """
params:
  input: data/sales.csv
  output: out/report.json
  smoothing: 2

alias:
  apply: /series/proj/apply

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
  params: {passes: $smoothing$, window: 5}
  builder: /builder/proj/compose

flow:
  load: {uri: /io/proj/read_csv, params: {path: $input$}, outputs: [raw]}
  smooth: {uri: apply, params: {fn: "@smoother"}, inputs: [raw], outputs: [smoothed]}
  save: {uri: /io/proj/write_json, params: {path: $output$}, inputs: [smoothed], outputs: []}
"""


def test_clean_recipe(write):
    path = write("main.yaml", FULL)
    assert check([path]) == []


def test_resolve_returns_recipe(write):
    path = write("main.yaml", FULL)
    data = resolve([path])
    assert data["flow"]["load"]["params"]["path"] == "data/sales.csv"
    assert data["flow"]["smooth"]["uri"] == "/series/proj/apply"
    assert data["smoother"]["params"]["passes"] == 2
    assert "blocks" in data


def test_gate_raises_with_rendered_problems(write):
    path = write("main.yaml", FULL + "\nextra:\n  uri: nope\n")
    with pytest.warns(CirakWarning, match="unused_component"), pytest.raises(ConfigError) as caught:
        resolve([path])
    text = str(caught.value)
    assert "1 problem found:" in text
    assert "[unknown_alias]" in text


def test_bool_branch_labels_pass(write):
    path = write("main.yaml", """
flow:
  pick:
    branch:
      decide: /c/p/balanced
      cases:
        true: {uri: /a/b/c, outputs: [x]}
        false: {uri: /a/b/d, outputs: [x]}
""")
    assert check([path]) == []


def test_variant_selection_by_file(write):
    base = write("base.yaml", "flow:\n  work: {uri: smooth, outputs: [done]}\n")
    fast = write("fast.yaml", "alias:\n  smooth: /series/statlib/rolling_mean\n")
    precise = write("precise.yaml", "alias:\n  smooth: /series/statlib/savgol\n")
    one = resolve([base, fast])
    two = resolve([base, precise])
    assert one["flow"]["work"]["uri"] == "/series/statlib/rolling_mean"
    assert two["flow"]["work"]["uri"] == "/series/statlib/savgol"
    both = check([base, fast, precise])
    assert [problem.kind for problem in both] == ["merge_conflict"]
