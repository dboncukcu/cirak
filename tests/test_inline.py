import pytest

from cirak import Deferred, check, lego, resolve, run
from cirak.api import analyze
from cirak.build import build_components


def kinds(problems):
    return [problem.kind for problem in problems]


def store_for(paths):
    analysis = analyze(paths)
    assert [problem for problem in analysis.problems if problem.severity == "error"] == []
    return build_components(analysis.data, analysis.expansions)


def test_inline_component_is_built_at_compile_time(write):
    class Policy:
        def __init__(self, monitor, mode="min"):
            self.monitor, self.mode = monitor, mode

    def checkpoint(policy, every=1):
        return policy

    lego("/ckpt/test/best", Policy, kind="checkpoint")
    lego("/ckpt/test/checkpoint", checkpoint)
    path = write("a.yaml", """
alias:
  best: /ckpt/test/best
saver: {uri: /ckpt/test/checkpoint, params: {policy: {uri: best, params: {monitor: val/rmse}}}}
""")
    assert check([path]) == [] or kinds(check([path])) == ["unused_component"]
    assert resolve([path])["saver"]["params"]["policy"]["uri"] == "/ckpt/test/best"
    built = store_for([path]).get("saver")
    assert isinstance(built, Policy)
    assert (built.monitor, built.mode) == ("val/rmse", "min")


def test_inline_component_partial_by_fact_or_by_key(write):
    def criterion(predictions, targets, delta=1.0):
        return abs(predictions - targets) * delta

    def scale(value, factor):
        return value * factor

    def adapter(criterion, output=None):
        return criterion

    lego("/criterion/test/huber", criterion, kind="criterion", partial=True)
    lego("/num/test/scale", scale)
    lego("/adapter/test/criterion", adapter, kind="adapter")
    path = write("a.yaml", """
huber: {uri: /adapter/test/criterion, params: {criterion: {uri: /criterion/test/huber, params: {delta: 2.0}}}}
doubler: {uri: /adapter/test/criterion, params: {criterion: {uri: /num/test/scale, params: {factor: 2}, partial: true}}}
""")
    assert not [problem for problem in check([path]) if problem.severity == "error"]
    store = store_for([path])
    assert store.get("huber")(3.0, 1.0) == 4.0
    assert store.get("doubler")(5) == 10


def test_data_kind_is_deferred_not_built(write):
    calls = []

    def class_weights(frame, power=1.0):
        calls.append((frame, power))
        return "weights"

    def cross_entropy(weight=None):
        return weight

    lego("/data/test/class_weights", class_weights, kind="data")
    lego("/criterion/test/ce", cross_entropy, kind="criterion")
    path = write("a.yaml", """
losses:
  ce: {uri: /criterion/test/ce, params: {weight: {uri: /data/test/class_weights, params: {power: 0.5}}}}
""")
    assert not [problem for problem in check([path]) if problem.severity == "error"]
    deferred = store_for([path]).get("losses.ce")
    assert isinstance(deferred, Deferred)
    assert deferred.uri == "/data/test/class_weights"
    assert deferred.params == {"power": 0.5}
    assert calls == []
    assert deferred.build(frame="train") == "weights"
    assert calls == [("train", 0.5)]


def test_inline_component_inside_flow_step_params(write):
    def schedule(start, end):
        return (start, end)

    def turn(schedule, loader):
        return f"{schedule}:{loader}"

    lego("/schedule/test/linear", schedule, kind="schedule")
    lego("/turn/test/turn", turn)
    lego("/g/test/loader", lambda: "L", returns="loader")
    path = write("a.yaml", """
flow:
  outputs: [turn]
  loader: {uri: /g/test/loader}
  turn: {uri: /turn/test/turn, params: {schedule: {uri: /schedule/test/linear, params: {start: 1, end: 2}}}}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"turn": "(1, 2):L"}


def test_inline_components_are_validated(write):
    lego("/schedule/test/steps", lambda size, gamma=0.1: (size, gamma), kind="schedule")
    path = write("a.yaml", """
one: {uri: /a/b/c, params: {s: {uri: /schedule/test/steps, params: {size: 1, wrong: 2}}}}
two: {uri: /a/b/c, params: {s: {uri: /schedule/test/steps}}}
three: {uri: /a/b/c, params: {s: {uri: /schedule/test/nope, params: {size: 1}}}}
four: {uri: /a/b/c, params: {s: {uri: nope_alias}}}
five: {uri: /a/b/c, params: {s: {uri: /schedule/test/steps, params: {size: 1}, extra: 1, partial: 3}}}
flow:
  step: {uri: /a/b/c, params: {s: {uri: /schedule/test/steps, params: {size: 1, wrong: 2}}}, outputs: []}
""")
    problems = check([path])
    found = kinds(problems)
    assert found.count("signature_mismatch") == 3
    assert found.count("unknown_uri") == 1
    assert found.count("unknown_alias") == 1
    assert found.count("invalid_component") == 2
    located = [problem for problem in problems if problem.kind == "signature_mismatch"]
    assert all(problem.line is not None for problem in located)


def test_nested_inline_components(write):
    lego("/pre/test/log", lambda base: f"log{base}", kind="pre")
    lego("/pre/test/wrap", lambda inner: f"[{inner}]", kind="pre")
    lego("/g/test/hold", lambda value: value)
    path = write("a.yaml", """
thing: {uri: /g/test/hold, params: {value: {uri: /pre/test/wrap, params: {inner: {uri: /pre/test/log, params: {base: 10}}}}}}
""")
    assert not [problem for problem in check([path]) if problem.severity == "error"]
    assert store_for([path]).get("thing") == "[log10]"


def test_inline_component_in_setup_is_rejected(write):
    path = write("a.yaml", """
setup:
  - {uri: /a/b/c, params: {x: {uri: /a/b/d}}}
""")
    assert "invalid_setup" in kinds(check([path]))


def test_check_does_not_build_inline_components(write):
    calls = []

    def factory(n):
        calls.append(n)
        return n

    lego("/g/test/factory", factory)
    lego("/g/test/hold2", lambda value: value)
    path = write("a.yaml", """
flow:
  outputs: [hold]
  hold: {uri: /g/test/hold2, params: {value: {uri: /g/test/factory, params: {n: 3}}}}
""")
    assert check([path]) == []
    assert calls == []
    assert run([path]).outputs == {"hold": 3}
    assert calls == [3]
