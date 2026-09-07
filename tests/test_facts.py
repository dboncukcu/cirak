import sys

import pytest

from cirak import ConfigError, RegistryError, check, lego, register, run
from cirak.registry import UNSET, Facts, Registry


def kinds(problems):
    return [problem.kind for problem in problems]


def test_lego_stores_normalized_facts():
    fresh = Registry()
    fresh.declare_kinds("turn")
    assert fresh.kinds == ["builder", "predicate", "data", "turn"]

    @fresh.lego("/turn/test/train", kind="turn", alias=["train", "supervised"],
                returns=["models", "metrics"], bus=["device"], mutates=["models"],
                state=["models"], refs={"loss": "loss"}, uses=["predicts"], needs_grad=True,
                needs_models=["net"], description="a turn")
    def train(models, loader, loss, device="cpu"):
        return {"models": models, "metrics": {}}

    facts = fresh.lookup("/turn/test/train").facts
    assert facts.kind == "turn"
    assert facts.alias == ("train", "supervised")
    assert facts.returns == ["models", "metrics"]
    assert facts.bus == {"device": "device"}
    assert facts.mutates == ("models",)
    assert facts.state == ("models",)
    assert facts.refs == {"loss": "loss"}
    assert facts.uses == ("predicts",)
    assert facts.needs_grad is True
    assert facts.needs_models == ("net",)
    assert facts.partial is False
    assert fresh.facts("/turn/test/train") is facts
    assert fresh.facts("/never/registered/uri") == Facts()
    assert fresh.aliases() == {"train": "/turn/test/train", "supervised": "/turn/test/train"}
    assert facts.declared() == {"kind": "turn", "alias": ["train", "supervised"],
                                "returns": ["models", "metrics"], "bus": {"device": "device"},
                                "mutates": ["models"], "state": ["models"], "refs": {"loss": "loss"},
                                "uses": ["predicts"], "needs_grad": True, "needs_models": ["net"]}


def test_extras_fact_is_stored_without_a_signature_check():
    fresh = Registry()
    fresh.declare_kinds("turn")

    def turn(models, params, extra):
        return models

    fresh.lego("/turn/test/alternating", turn, kind="turn", extras=["amp", "grad_clip", "accumulate"])
    facts = fresh.facts("/turn/test/alternating")
    assert facts.extras == ("amp", "grad_clip", "accumulate")
    assert facts.declared()["extras"] == ["amp", "grad_clip", "accumulate"]
    fresh.lego("/turn/test/one", turn, kind="turn", extras="amp")
    assert fresh.facts("/turn/test/one").extras == ("amp",)
    with pytest.raises(RegistryError, match="extras must be a string or a list"):
        fresh.lego("/turn/test/bad", turn, kind="turn", extras=3)


def test_returns_none_differs_from_no_returns_fact():
    fresh = Registry()
    fresh.register("/a/b/effect", lambda: None, returns=None)
    fresh.register("/a/b/plain", lambda: None)
    assert fresh.facts("/a/b/effect").returns is None
    assert fresh.facts("/a/b/plain").returns is UNSET
    assert fresh.facts("/a/b/effect").declared() == {"returns": None}
    assert fresh.facts("/a/b/plain").declared() == {}


def test_registration_time_checks():
    fresh = Registry()

    def fn(a, b=1):
        return a

    with pytest.raises(RegistryError, match="unknown facts"):
        fresh.register("/a/b/c", fn, colour="red")
    with pytest.raises(RegistryError, match="kind 'thing' is not declared"):
        fresh.register("/a/b/c", fn, kind="thing")
    with pytest.raises(RegistryError, match="kind names must be strings"):
        fresh.declare_kinds("")
    fresh.declare_kinds("lego")
    fresh.declare_kinds("lego")
    assert fresh.kinds.count("lego") == 1
    with pytest.raises(RegistryError, match="bus lists 'a'"):
        fresh.register("/a/b/c", fn, bus=["a"])
    with pytest.raises(RegistryError, match="bus lists 'zzz'"):
        fresh.register("/a/b/c", fn, bus={"zzz": "*_x"})
    with pytest.raises(RegistryError, match="mutates names 'nope'"):
        fresh.register("/a/b/c", fn, mutates=["nope"])
    with pytest.raises(RegistryError, match="aliases names 'nope'"):
        fresh.register("/a/b/c", fn, aliases="nope")
    with pytest.raises(RegistryError, match="refs names 'nope'"):
        fresh.register("/a/b/c", fn, refs={"nope": "model"})
    with pytest.raises(RegistryError, match="state names 'q'"):
        fresh.register("/a/b/c", fn, returns=["p"], state=["q"])
    with pytest.raises(RegistryError, match="returns must be a list"):
        fresh.register("/a/b/c", fn, returns="p", state=["p"])
    with pytest.raises(RegistryError, match="mutates names 'a' but returns"):
        fresh.register("/a/b/c", fn, returns=["p"], mutates=["a"])
    with pytest.raises(RegistryError, match="alias 'x/y'"):
        fresh.register("/a/b/c", fn, alias="x/y")
    with pytest.raises(RegistryError, match="partial must be a boolean"):
        fresh.register("/a/b/c", fn, partial="yes")
    fresh.register("/a/b/c", fn, alias="short", bus=["b"], mutates=["a"], aliases=["b"],
                   refs={"a": "model"}, kind="lego")
    with pytest.raises(RegistryError, match="alias 'short' is already taken"):
        fresh.register("/a/b/d", fn, alias="short")

    def loose(**kwargs):
        return kwargs

    fresh.register("/a/b/loose", loose, mutates=["anything"], refs={"any": "model"})
    with pytest.raises(RegistryError, match="bus lists 'anything'"):
        fresh.register("/a/b/loose2", loose, bus=["anything"])


def test_lazy_target_is_checked_when_resolved(tmp_path, monkeypatch):
    module = tmp_path / "facts_probe_mod.py"
    module.write_text("def target(x):\n    return x\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("facts_probe_mod", None)
    fresh = Registry()
    fresh.register("/num/probe/lazy", "facts_probe_mod:target", bus=["x"])
    with pytest.raises(RegistryError, match="bus lists 'x'"):
        fresh.resolve("/num/probe/lazy")
    sys.modules.pop("facts_probe_mod", None)


def test_identical_reregistration_compares_facts():
    fresh = Registry()
    fresh.declare_kinds("lego")

    def fn():
        return 1

    fresh.register("/a/b/c", fn, kind="lego")
    fresh.register("/a/b/c", fn, kind="lego")
    with pytest.raises(RegistryError):
        fresh.register("/a/b/c", fn, kind="predicate")


def test_ls_filters_by_kind():
    fresh = Registry()
    fresh.register("/a/b/pred", lambda: True, kind="predicate")
    fresh.register("/a/b/plain", lambda: 1)
    assert [entry.uri for entry in fresh.ls("/", kind="predicate")] == ["/a/b/pred"]
    assert len(fresh.ls("/")) == 2


def test_returns_fact_fills_outputs(write):
    calls = []

    def table():
        return {"rows": 3}

    def split(table):
        return {"train": table["rows"] - 1, "test": 1, "extra": 0}

    def log(train):
        calls.append(train)

    def whole(table):
        return table["rows"] * 2

    lego("/f/test/table", table, returns="df")
    lego("/f/test/split", split, returns=["train", "test"])
    lego("/f/test/log", log, returns=None)
    lego("/f/test/whole", whole)
    path = write("a.yaml", """
flow:
  outputs: [train, test, doubled]
  load: {uri: /f/test/table}
  parts: {uri: /f/test/split, inputs: {table: df}}
  doubled: {uri: /f/test/whole, inputs: {table: df}}
  report: {uri: /f/test/log}
""")
    assert check([path]) == []
    report = run([path])
    assert report.outputs == {"train": 2, "test": 1, "doubled": 6}
    assert calls == [2]


def test_yaml_outputs_override_returns_fact(write):
    def split(table):
        return {"train": 1, "test": 2}

    lego("/f/test/split2", split, returns=["train", "test"])
    lego("/f/test/three", lambda: 3, returns="x")
    path = write("a.yaml", """
flow:
  outputs: [whole, tr]
  seed: {uri: /f/test/three, outputs: [value]}
  whole: {uri: /f/test/split2, inputs: {table: value}, outputs: [whole]}
  picked: {uri: /f/test/split2, inputs: {table: value}, outputs: {train: tr}}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"whole": {"train": 1, "test": 2}, "tr": 1}


def test_bus_fact_binds_defaulted_parameters_implicitly(write):
    seen = []

    def work(value, device="cpu", tag="none"):
        seen.append((value, device, tag))
        return value

    lego("/f/test/work", work, bus=["device", "tag"])
    lego("/f/test/dev", lambda: "cuda", returns="device")
    lego("/f/test/one", lambda: 1, returns="value")
    path = write("a.yaml", """
flow:
  outputs: [work]
  device: {uri: /f/test/dev}
  value: {uri: /f/test/one}
  work: {uri: /f/test/work, params: {tag: fixed}}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"work": 1}
    assert seen == [(1, "cuda", "fixed")]


def test_mutates_fact_gives_passthrough_to_a_gated_step(write):
    def state():
        return {"n": 0}

    def flag():
        return False

    def touch(state):
        state["n"] += 1
        return state

    lego("/f/test/state", state, returns="state")
    lego("/f/test/flag", flag, returns="go")
    lego("/f/test/touch", touch, mutates=["state"])
    path = write("a.yaml", """
flow:
  outputs: [state_next]
  state: {uri: /f/test/state}
  go: {uri: /f/test/flag}
  touch: {uri: /f/test/touch, inputs: [state], outputs: {state: state_next}, when: go}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"state_next": {"n": 0}}


def test_gated_step_without_covering_facts_is_a_tezgah_problem(write):
    lego("/f/test/state3", lambda: {"n": 0}, returns="state")
    lego("/f/test/flag3", lambda: False, returns="go")
    lego("/f/test/touch3", lambda state: state)
    path = write("a.yaml", """
flow:
  outputs: [state_next]
  state: {uri: /f/test/state3}
  go: {uri: /f/test/flag3}
  touch: {uri: /f/test/touch3, inputs: [state], outputs: [state_next], when: go}
""")
    assert "tezgah_validation" in kinds(check([path]))
    with pytest.raises(ConfigError) as caught:
        run([path])
    assert "tezgah_validation" in kinds(caught.value.problems)


def test_condition_targets_must_be_predicates(write):
    lego("/f/test/is_big", lambda n: n > 2, kind="predicate")
    lego("/f/test/not_pred", lambda n: n, kind="metric")
    lego("/f/test/untyped", lambda n: n > 1)
    path = write("a.yaml", """
flow:
  seed: {uri: /a/b/c, outputs: [n]}
  ok: {uri: /a/b/c, inputs: [n], outputs: [], when: {uri: /f/test/is_big}}
  fine: {uri: /a/b/c, inputs: [n], outputs: [], when: {uri: /f/test/untyped}}
  bad: {uri: /a/b/c, inputs: [n], outputs: [], when: {uri: /f/test/not_pred}}
  slash: {uri: /a/b/c, inputs: [n], outputs: [], when: /f/test/is_big}
  pick:
    branch:
      decide: /f/test/not_pred
      cases:
        true: {uri: /a/b/c, inputs: [n], outputs: [t]}
""")
    found = kinds(check([path]))
    assert found.count("kind_mismatch") == 2
    assert "invalid_condition" in found


def test_builder_field_must_be_a_builder(write):
    lego("/builder/test/real", lambda graph: graph, kind="builder")
    lego("/builder/test/fake", lambda graph: graph, kind="layer")
    path = write("a.yaml", """
blocks:
  chain:
    spec:
      - {uri: /a/b/c, partial: true}
good: {block: chain, builder: /builder/test/real}
bad: {block: chain, builder: /builder/test/fake}
""")
    problems = [problem for problem in check([path]) if problem.kind == "kind_mismatch"]
    assert len(problems) == 1
    assert "bad" in problems[0].message


def test_partial_fact_makes_components_callables(write):
    def scale(value, factor):
        return value * factor

    lego("/f/test/scale", scale, partial=True)
    lego("/f/test/apply", lambda fn, x: fn(x))
    lego("/f/test/seven", lambda: 7, returns="x")
    path = write("a.yaml", """
doubler: {uri: /f/test/scale, params: {factor: 2}}
flow:
  outputs: [answer]
  seed: {uri: /f/test/seven}
  apply: {uri: /f/test/apply, params: {fn: "@doubler"}, inputs: [x], outputs: [answer]}
""")
    assert check([path]) == []
    assert run([path]).outputs == {"answer": 14}


def test_flow_step_signature_checks(write):
    def work(a, b, c=1):
        return a + b + c

    lego("/f/test/work2", work)
    path = write("a.yaml", """
flow:
  seed: {uri: /a/b/c, outputs: [a]}
  both: {uri: /f/test/work2, params: {a: 1}, inputs: [a], outputs: []}
  unknown: {uri: /f/test/work2, params: {zzz: 1}, inputs: {a: a, b: a}, outputs: []}
  missing: {uri: /f/test/work2, inputs: [a], outputs: []}
  badin: {uri: /f/test/work2, inputs: {a: a, b: a, q: a}, outputs: []}
""")
    problems = check([path])
    found = kinds(problems)
    assert "double_binding" in found
    assert found.count("signature_mismatch") == 4
    assert any("requires parameter 'b'" in problem.message for problem in problems)


def test_predicate_bus_facts_reach_tezgah(write):
    seen = []

    def over(n, limit=100, tag="none"):
        seen.append((n, limit, tag))
        return n > limit

    def over_next(n_next, limit=100):
        seen.append(("until", n_next, limit))
        return n_next > limit

    def label(n, mode="even"):
        return (n % 2 == 0) if mode == "even" else (n % 2 == 1)

    lego("/f/test/over", over, kind="predicate", bus=["limit", "tag"])
    lego("/f/test/over_next", over_next, kind="predicate", bus=["limit"])
    lego("/f/test/label", label, kind="predicate", bus=["mode"])
    lego("/f/test/seven", lambda: 7, returns="n")
    lego("/f/test/three", lambda: 3, returns="limit")
    lego("/f/test/odd", lambda: "odd", returns="mode")
    lego("/f/test/bump", lambda n: n + 1, returns="n_next")
    lego("/f/test/shout", lambda n: seen.append("shout"))
    path = write("a.yaml", """
flow:
  outputs: [final, picked]
  n: {uri: /f/test/seven}
  limit: {uri: /f/test/three}
  mode: {uri: /f/test/odd}
  alarm: {uri: /f/test/shout, inputs: [n], outputs: [], when: {uri: /f/test/over, params: {tag: fixed}}}
  climb:
    loop:
      carry: [n]
      next: _next
      range: 5
      until: {uri: /f/test/over_next, params: {limit: 9}}
      outputs: {n: final}
      body: {uri: /f/test/bump, inputs: {n: n}, outputs: [n_next]}
  pick:
    branch:
      decide: /f/test/label
      cases:
        true: {uri: /f/test/seven, outputs: [picked]}
        false: {uri: /f/test/three, outputs: [picked]}
""")
    assert check([path]) == []
    report = run([path])
    assert report.outputs["final"] == 10
    assert report.outputs["picked"] == 7
    assert seen[0] == (7, 3, "fixed")
    assert "shout" in seen
    assert [entry for entry in seen if isinstance(entry, tuple) and entry[0] == "until"] == [
        ("until", 8, 9), ("until", 9, 9), ("until", 10, 9)]
