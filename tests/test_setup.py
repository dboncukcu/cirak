from cirak import check, register, run


def test_setup_runs_before_components_are_built(write):
    order = []

    def mark(tag):
        order.append(("setup", tag))

    def component():
        order.append(("build", None))
        return object()

    def use(thing):
        return "ok"

    register("/t/setup/mark", mark, description="d")
    register("/t/setup/component", component, description="d")
    register("/t/setup/use", use, description="d")
    path = write("a.yaml", """
params:
  tag: s1
setup:
  - {uri: /t/setup/mark, params: {tag: $tag$}}
things:
  one: {uri: /t/setup/component}
flow:
  outputs: [answer]
  step: {uri: /t/setup/use, params: {thing: "@things.one"}, outputs: [answer]}
""")
    report = run([path])
    assert report.outputs == {"answer": "ok"}
    assert order[0] == ("setup", "s1")
    assert ("build", None) in order


def test_setup_is_validated_without_running(write):
    path = write("a.yaml", """
setup:
  - {uri: /nope/missing}
  - {params: {x: 1}}
  - {uri: /a/b/c, params: {value: "@things.one"}, extra: 1}
""")
    kinds = {problem.kind for problem in check([path])}
    assert "unknown_uri" in kinds
    assert "invalid_setup" in kinds


def test_setup_signature_is_checked(write):
    def mark(tag):
        return tag

    register("/t/setup/mark", mark, description="d")
    path = write("a.yaml", """
setup:
  - {uri: /t/setup/mark}
  - {uri: /t/setup/mark, params: {nope: 1}}
""")
    messages = [problem.message for problem in check([path])
                if problem.kind == "signature_mismatch"]
    assert len(messages) == 3
    assert any("requires parameter 'tag' (at setup[0])" in message for message in messages)
    assert any("has no parameter 'nope'" in message for message in messages)
    assert any("requires parameter 'tag' (at setup[1])" in message for message in messages)
