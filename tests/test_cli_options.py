import pytest

from cirak import lego
from cirak.cli import main


def test_set_overrides_a_leaf_with_a_yaml_value(write, capsys, tmp_path):
    lego("/c/test/const", lambda value: value)
    path = write("a.yaml", """
params:
  n: 1
flow:
  outputs: [x]
  seed: {uri: /c/test/const, params: {value: $n$}, outputs: [x]}
""")
    record = tmp_path / "rec"
    assert main(["run", path, "--set", "params.n=[1, 2]", "--record", str(record)]) == 0
    assert "x: [1, 2]" in capsys.readouterr().out
    text = (record / "resolved.yaml").read_text()
    assert "--set overrides" in text and "a.yaml:3" in text
    assert (record / "flow.yaml").exists()


def test_set_needs_path_equals_value(write, capsys):
    path = write("a.yaml", "params:\n  n: 1\n")
    with pytest.raises(SystemExit) as caught:
        main(["check", path, "--set", "params.n"])
    assert caught.value.code == 2
    assert "PATH=VALUE" in capsys.readouterr().err


def test_check_layers_prints_the_tree(write, capsys):
    write("base.yaml", "params:\n  n: 1\n")
    main_file = write("top.yaml", "include: [base.yaml]\nparams:\n  n: 2\n")
    assert main(["check", main_file, "--layers", "--set", "params.n=3"]) == 0
    out = capsys.readouterr().out
    assert "layers, bottom to top:" in out
    assert "1. base.yaml (included by top.yaml)" in out
    assert "2. top.yaml" in out and "overrides params.n (" in out
    assert "3. --set" in out
    assert "no problems found" in out


def test_check_and_run_take_inputs(write, capsys):
    lego("/c/test/place", lambda device: device)
    path = write("a.yaml", "flow:\n  outputs: [placed]\n  place: {uri: /c/test/place, outputs: [placed]}\n")
    assert main(["check", path]) == 1
    assert "[missing_input]" in capsys.readouterr().out
    assert main(["check", path, "--input", "device"]) == 0
    capsys.readouterr()
    assert main(["run", path, "--input", "device=cuda:1"]) == 0
    assert "placed: 'cuda:1'" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["run", path, "--input", "device"])


def test_show_flow_prints_the_expanded_flow(write, capsys):
    lego("/c/test/frame", lambda set: set)
    path = write("a.yaml", """
blocks:
  data:
    flow:
      frame:
        foreach: {over: [train, test], item: set, key: $set$, node: {uri: /c/test/frame, params: {set: $set$}, outputs: [$set$_frame]}}
flow:
  outputs: [train_frame]
  data: {block: data}
""")
    assert main(["show", path, "--flow"]) == 0
    out = capsys.readouterr().out
    assert "frame_train:" in out and "foreach" not in out
    assert "# exports: train_frame" in out
    bad = write("b.yaml", "flow:\n  x: {uri: /c/test/nope}\n")
    assert main(["show", bad, "--flow"]) == 1
    assert "[unknown_uri]" in capsys.readouterr().err


def test_ls_prints_kind_and_facts(capsys):
    lego("/turn/test/train", lambda models, device="cpu": models, kind="turn", returns=["models"],
         bus=["device"], mutates=["models"], description="train once")
    assert main(["ls", "/turn/test"]) == 0
    out = capsys.readouterr().out
    assert "/turn/test/train" in out and "turn" in out and "train once" in out
    assert "returns: models" in out and "bus: device=device" in out and "mutates: models" in out
    assert main(["ls", "/", "--kind", "turn"]) == 0
    out = capsys.readouterr().out
    assert "/turn/test/train" in out and "/a/b/c" not in out
