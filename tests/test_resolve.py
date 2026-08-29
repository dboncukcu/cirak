from cirak.loader import load
from cirak.merge import merge
from cirak.resolve import resolve


def kinds(problems):
    return [problem.kind for problem in problems]


def resolved(paths):
    files, load_problems = load(paths)
    assert load_problems == []
    data, provenance, merge_problems = merge(files)
    assert merge_problems == []
    return resolve(data, provenance)


def test_alias_resolution_and_chain(write):
    path = write("a.yaml", """
alias:
  fast: quick
  quick: /series/statlib/rolling_mean
smoother_fn:
  uri: fast
  partial: true
""")
    data, problems = resolved([path])
    assert problems == []
    assert data["smoother_fn"]["uri"] == "/series/statlib/rolling_mean"


def test_alias_cycle(write):
    path = write("a.yaml", "alias:\n  a: b\n  b: a\nc:\n  uri: a\n")
    _, problems = resolved([path])
    assert kinds(problems) == ["alias_cycle"]


def test_unknown_alias(write):
    path = write("a.yaml", "c:\n  uri: nope\n")
    _, problems = resolved([path])
    assert kinds(problems) == ["unknown_alias"]


def test_forbidden_placeholder_in_uri(write):
    path = write("a.yaml", "params:\n  u: /a/b/c\nc:\n  uri: $u$\n")
    _, problems = resolved([path])
    assert kinds(problems) == ["forbidden_placeholder"]


def test_global_substitution_preserves_types(write):
    path = write("a.yaml", """
params:
  n: 3
  name: model
c:
  uri: /a/b/c
  partial: true
  params:
    window: $n$
    label: run_$name$_v$n$
    shout: $$plain
""")
    data, problems = resolved([path])
    assert problems == []
    assert data["c"]["params"]["window"] == 3
    assert data["c"]["params"]["label"] == "run_model_v3"
    assert data["c"]["params"]["shout"] == "$plain"


def test_unknown_variable(write):
    path = write("a.yaml", "c:\n  uri: /a/b/c\n  params: {w: $missing$}\n")
    _, problems = resolved([path])
    assert kinds(problems) == ["unknown_variable"]


def test_block_local_variables_wait_for_expansion(write):
    path = write("a.yaml", """
params:
  outside: 7
blocks:
  chain:
    variables:
      w: {default: 3}
    spec:
      - uri: /a/b/c
        partial: true
        params: {window: $w$, level: $outside$}
""")
    data, problems = resolved([path])
    assert problems == []
    item = data["blocks"]["chain"]["spec"][0]
    assert item["params"]["window"] == "$w$"
    assert item["params"]["level"] == 7


def test_params_section_stays_literal(write):
    path = write("a.yaml", "params:\n  a: $b$\n  b: 2\n")
    data, problems = resolved([path])
    assert problems == []
    assert data["params"]["a"] == "$b$"
