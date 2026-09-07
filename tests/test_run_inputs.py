import pytest

from cirak import ConfigError, check, lego, run


def kinds(problems):
    return [problem.kind for problem in problems]


RECIPE = """
params:
  device: never_on_the_bus
flow:
  outputs: [placed]
  place: {uri: /r/test/place, outputs: [placed]}
"""


def test_inputs_are_root_bus_keys_and_params_are_not(write):
    seen = []

    def place(device, record):
        seen.append((device, record))
        return f"{device}:{record}"

    lego("/r/test/place", place)
    path = write("a.yaml", RECIPE)
    assert check([path], inputs=["device", "record"]) == []
    report = run([path], inputs={"device": "cpu", "record": "runs/x"})
    assert report.outputs == {"placed": "cpu:runs/x"}
    assert seen == [("cpu", "runs/x")]


def test_check_without_the_inputs_reports_missing_keys_with_location(write):
    lego("/r/test/place", lambda device, record: device)
    path = write("a.yaml", RECIPE)
    problems = check([path])
    assert kinds(problems) == ["missing_input", "missing_input"]
    assert {problem.message.split("'")[1] for problem in problems} == {"device", "record"}
    assert problems[0].file == path
    assert problems[0].line == 6
    assert kinds(check([path], inputs=["device"])) == ["missing_input"]


def test_unexpected_input_is_an_error_in_check_and_run(write):
    lego("/r/test/place", lambda device, record: device)
    path = write("a.yaml", RECIPE)
    assert kinds(check([path], inputs=["device", "record", "extra"])) == ["unexpected_input"]
    with pytest.raises(ConfigError) as caught:
        run([path], inputs={"device": "cpu", "record": "r", "extra": 1})
    assert kinds(caught.value.problems) == ["unexpected_input"]


def test_run_without_a_required_input_is_a_config_error(write):
    lego("/r/test/place", lambda device, record: device)
    path = write("a.yaml", RECIPE)
    with pytest.raises(ConfigError) as caught:
        run([path], inputs={"device": "cpu"})
    assert kinds(caught.value.problems) == ["missing_input"]


def test_check_never_builds_components(write):
    built = []

    def factory():
        built.append(True)
        return object()

    lego("/r/test/factory", factory)
    lego("/r/test/use", lambda thing: "ok")
    path = write("a.yaml", """
things:
  one: {uri: /r/test/factory}
flow:
  outputs: [answer]
  step: {uri: /r/test/use, params: {thing: "@things.one"}, outputs: [answer]}
""")
    assert check([path]) == []
    assert built == []
    assert run([path]).outputs == {"answer": "ok"}
    assert built == [True]


def test_sinks_receive_tezgah_events(write):
    lego("/r/test/five", lambda: 5, returns="x")
    path = write("a.yaml", "flow:\n  outputs: [x]\n  seed: {uri: /r/test/five}\n")
    events = []
    run([path], sinks=[events.append])
    assert [event["kind"] for event in events if event["path"] == "seed"] == ["started", "finished"]


def test_check_sees_implicit_bindings_of_run_inputs(write):
    seen = []

    def work(value, device="cpu"):
        seen.append(device)
        return value

    lego("/r/test/work", work, bus=["device"])
    lego("/r/test/one", lambda: 1, returns="value")
    path = write("a.yaml", """
flow:
  outputs: [work]
  value: {uri: /r/test/one}
  work: {uri: /r/test/work}
""")
    assert check([path]) == []
    assert check([path], inputs=["device"]) == []
    assert kinds(check([path], inputs=["gpu"])) == ["unexpected_input"]
    assert run([path], inputs={"device": "cuda"}).outputs == {"work": 1}
    assert seen == ["cuda"]
