"""The stage two integration test: kalfa's template plus a mock registry reproduces the two target dumps.

Every fixture comes from kalfa-v2, synced by tests/fixtures/regenerate_dumps.py: kalfa.yaml is
src/kalfa/templates/kalfa.yaml, the two recipes are kalfa's driver documents (configs/dumps/*.recipe.yaml, include
rewritten) and 01.flow.yaml and tidy.flow.yaml are kalfa's dumps. kalfa is the only producer; the comparison is
plain equality of the parsed documents (comments are not data).
"""

import json
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from cirak import check, run
from cirak.api import analyze, dump_document, dump_text, effective_flow, flow_dump
from kalfa_mock import Model, register_all

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURES))
from regenerate_dumps import TARGETS, differences  # noqa: E402

INPUTS = ["device", "record"]
CASES = list(TARGETS)


def load_dump(name):
    return YAML(typ="safe").load((FIXTURES / name).read_text())


def plain(value):
    return json.loads(json.dumps(value, sort_keys=True, default=repr))


def analysis_of(recipe):
    analysis = analyze([str(FIXTURES / recipe)])
    assert [problem for problem in analysis.problems if problem.severity == "error"] == []
    return analysis


def test_01_checks_clean_with_the_run_inputs():
    register_all()
    assert check([str(FIXTURES / "01.recipe.yaml")], inputs=INPUTS) == []
    assert check([str(FIXTURES / "01.recipe.yaml")]) == []
    assert [problem.kind for problem in check([str(FIXTURES / "01.recipe.yaml")], inputs=["gpu"])] == ["unexpected_input"]
    with_inputs = flow_dump([str(FIXTURES / "01.recipe.yaml")], inputs=INPUTS)
    without = flow_dump([str(FIXTURES / "01.recipe.yaml")])
    assert "# implicit: device" in with_inputs and "implicit: device" not in without


def test_tidy_checks_clean_but_for_the_unused_wires():
    register_all()
    problems = check([str(FIXTURES / "tidy.recipe.yaml")], inputs=INPUTS)
    assert [problem.severity for problem in problems] == ["warning"] * 4
    messages = [problem.message for problem in problems]
    assert any("'feature'" in message and "flow.models.build_dxz" in message for message in messages)
    assert any("'feature'" in message and "flow.models.build_dzz" in message for message in messages)
    assert any("'logit_rec'" in message and "compose_anomaly_score" in message for message in messages)
    assert not any("build_dxx" in message for message in messages)


@pytest.mark.parametrize("recipe, dump", CASES)
def test_generated_document_equals_the_dump(recipe, dump):
    register_all()
    analysis = analysis_of(recipe)
    expected = load_dump(dump)
    assert set(expected) == {"components", "blocks", "flow"}
    reserved = {"include", "plugins", "alias", "params", "blocks", "setup", "flow"}
    components = {name: value for name, value in analysis.data.items() if name not in reserved}
    assert plain(components) == plain(expected["components"])
    blocks = {name: block for name, block in analysis.data["blocks"].items() if "flow" not in block}
    assert plain(blocks) == plain(expected["blocks"])
    assert plain(effective_flow(analysis.flow)) == plain(expected["flow"])


@pytest.mark.parametrize("recipe, dump", CASES)
def test_sync_script_finds_no_differences(recipe, dump):
    assert differences(recipe, dump) == []


def test_dump_document_nests_the_opened_blocks_with_full_uris():
    register_all()
    expected = load_dump("01.flow.yaml")
    flow = expected["flow"]
    assert list(flow) == ["outputs", "data", "models", "optimizers", "training", "after"]
    assert list(flow["data"]) == ["source", "split", "set_filter_train", "set_filter_valid", "set_filter_test", "prep",
                                  "frame_train", "frame_valid", "frame_test", "feed_train", "feed_valid", "feed_test",
                                  "loader_train", "loader_valid", "loader_test"]
    assert list(flow["models"]) == ["build_model", "models", "emas", "composites"]
    assert list(flow["optimizers"]) == ["build_opt_model", "optimizers"]
    loop = flow["training"]["epochs"]["loop"]
    assert loop["outputs"] == {"models": "models_final", "optimizers": "optimizers_final", "emas": "emas_final",
                               "counters": "counters_final", "rules": "rules_final"}
    body = loop["body"]
    assert list(body) == ["effects", "turn", "evaluate_valid", "evaluate_test", "metrics", "rules_0", "rule_1", "rule_2",
                          "rule_3", "rules_ruled", "stop", "checkpoint", "log"]
    assert body["evaluate_valid"]["outputs"] == ["valid_metrics"]
    assert body["rule_1"] == {"uri": "/rule/kalfa/rule", "params": {"name": "to_huber", "when": "@triggers.to_huber",
                                                                       "set": {"loss": "loss_huber"}, "after": None},
                              "inputs": {"rules": "rules_0"}, "outputs": ["rules_1"]}
    assert body["checkpoint"]["params"]["policy"]["uri"] == "/checkpoint/kalfa/best"
    assert body["checkpoint"]["outputs"] == []
    assert body["log"]["outputs"] == []
    assert flow["training"]["init"]["outputs"] == ["epochs_left"]
    assert flow["training"]["counters"]["outputs"] == ["counters"]
    assert flow["after"]["final"]["outputs"] == []
    assert flow["after"]["final"]["inputs"] == {"models": "models_final", "optimizers": "optimizers_final",
                                                "emas": "emas_final", "counters": "counters_final",
                                                "rules": "rules_final"}
    assert flow["after"]["report"]["inputs"] == {"models": "models_final", "emas": "emas_final"}
    text = flow_dump([str(FIXTURES / "01.recipe.yaml")], inputs=INPUTS)
    assert "# implicit: device" in text
    assert "parts *_metrics: test_metrics, train_metrics, valid_metrics" in text
    assert "# exports: prep, train_loader, valid_loader, test_loader" in text
    tidy = load_dump("tidy.flow.yaml")
    assert list(tidy["flow"]["data"])[:3] == ["source", "pre_1", "split"]
    assert tidy["flow"]["data"]["pre_1"]["inputs"] == {"df": "df_0"}
    assert tidy["flow"]["data"]["split"]["inputs"] == {"df": "df_1"}
    assert list(tidy["flow"]["models"]) == ["build_encoder", "build_generator", "build_dxz", "build_dxx", "build_dzz",
                                            "models", "emas", "compose_anomaly_score", "composites"]
    assert list(tidy["flow"]["optimizers"]) == ["build_opt_g", "build_opt_d", "optimizers"]
    assert tidy["flow"]["training"]["epochs"]["loop"]["body"]["rules_ruled"]["inputs"] == {"value": "rules_0"}


def run_recipe(recipe, executor, workers=None, record_dir=None):
    register_all()
    record = []
    report = run([str(FIXTURES / recipe)], inputs={"device": "cpu", "record": record}, executor=executor,
                 workers=workers, record_dir=record_dir)
    return report, record


def test_01_runs_on_tezgah_with_the_mock_registry(tmp_path):
    report, record = run_recipe("01.recipe.yaml", "serial", record_dir=str(tmp_path / "rec"))
    assert list(report.outputs) == ["history", "predictions"]
    history = report.outputs["history"]
    assert len(history) == 3
    assert set(history[0]) == {"train/loss", "train/active", "train/device", "val/score", "val/predicts",
                               "test/score", "test/predicts"}
    assert history[0]["train/device"] == "cpu"
    assert history[0]["val/predicts"] == "model"
    assert [entry["train/loss"] for entry in history] == [1.0, 0.5, pytest.approx(1 / 3)]
    assert report.outputs["predictions"] == {"predicts": "model", "set": "test", "rows": 2}
    state = ["counters_next", "emas_next", "models_next", "optimizers_next", "rules_next"]
    assert record.count(("checkpoint", state, "best")) == 3
    assert [entry for entry in record if entry[0] == "log"] == [("log", 0, "default"), ("log", 1, "default"),
                                                                 ("log", 2, "default")]
    assert record[-1] == ("final", ["model"], ["model"], 3)
    written = (tmp_path / "rec" / "flow.yaml").read_text()
    assert written == flow_dump([str(FIXTURES / "01.recipe.yaml")], inputs=INPUTS)
    assert (tmp_path / "rec" / "resolved.yaml").exists()


def test_tidy_runs_on_tezgah_and_builds_the_composite_from_the_models():
    report, record = run_recipe("tidy.recipe.yaml", "serial")
    history = report.outputs["history"]
    assert len(history) == 3
    assert history[0]["val/predicts"] == "anomaly_score"
    assert record[-1] == ("final", ["dxx", "dxz", "dzz", "encoder", "generator"], ["d", "g"], 3)
    assert record.count(("checkpoint", ["counters_next", "emas_next", "models_next", "optimizers_next",
                                        "rules_next"], None)) == 3


def test_serial_and_thread_agree():
    serial, serial_record = run_recipe("01.recipe.yaml", "serial")
    threaded, thread_record = run_recipe("01.recipe.yaml", "thread", workers=4)
    assert plain(serial.outputs) == plain(threaded.outputs)
    assert plain(serial_record) == plain(thread_record)


def test_events_carry_the_turn_total_and_models_change_in_place():
    register_all()
    seen = {}

    def sink(event):
        if event["kind"] == "started" and event["path"] == "training.epochs":
            seen["total"] = event["total"]

    record = []
    report = run([str(FIXTURES / "tidy.recipe.yaml")], inputs={"device": "cpu", "record": record}, sinks=[sink])
    assert seen["total"] == 3
    final = [entry for entry in record if entry[0] == "final"][0]
    assert final[3] == 3
    assert len(report.outputs["history"]) == 3


def test_flow_dump_does_not_build_anything():
    register_all()
    before = Model.built
    text = flow_dump([str(FIXTURES / "tidy.recipe.yaml")], inputs=INPUTS)
    assert Model.built == before
    assert "compose_anomaly_score:" in text
    document = YAML(typ="safe").load(text)
    assert dump_text(dump_document(analysis_of("tidy.recipe.yaml"), None)).startswith("components:")
    assert set(document) == {"components", "blocks", "flow"}
