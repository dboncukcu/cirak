import sys

import pytest

from cirak import register
from cirak.cli import main


def test_check_clean(write, capsys):
    path = write("a.yaml", "params:\n  n: 1\n")
    assert main(["check", path]) == 0
    assert "no problems" in capsys.readouterr().out


def test_check_errors_exit_1(write, capsys):
    path = write("a.yaml", "c: {uri: nope}\n")
    assert main(["check", path]) == 1
    assert "[unknown_alias]" in capsys.readouterr().out


def test_check_warnings_exit_0(write, capsys):
    path = write("a.yaml", "c: {uri: /a/b/c, partial: true}\n")
    assert main(["check", path]) == 0
    assert "[unused_component]" in capsys.readouterr().out


def test_show_prints_resolved(write, capsys):
    path = write("a.yaml", "alias:\n  x: /a/b/c\nc: {uri: x, partial: true}\n")
    assert main(["show", path]) == 0
    assert "/a/b/c" in capsys.readouterr().out


def test_show_expanded(write, capsys):
    path = write("a.yaml", """
blocks:
  chain:
    spec:
      - {uri: /a/b/c, partial: true}
c:
  block: chain
  builder: /builder/proj/compose
""")
    assert main(["show", path, "--expanded"]) == 0
    out = capsys.readouterr().out
    assert "---" in out
    assert "s0" in out


def test_show_errors_to_stderr(write, capsys):
    path = write("a.yaml", "c: {uri: nope}\n")
    assert main(["show", path]) == 1
    captured = capsys.readouterr()
    assert "[unknown_alias]" in captured.err
    assert captured.out == ""


def test_run_cli(write, capsys, tmp_path):
    def const(value):
        return value

    register("/num/cli/const", const, description="d")
    path = write("a.yaml", """
flow:
  outputs: [x]
  seed: {uri: /num/cli/const, params: {value: 5}, outputs: [x]}
""")
    record = tmp_path / "rec"
    assert main(["run", path, "--record", str(record)]) == 0
    out = capsys.readouterr().out
    assert ": ok" in out
    assert "x: 5" in out
    assert (record / "resolved.yaml").exists()


def test_run_cli_failure(write, capsys):
    path = write("a.yaml", "params:\n  n: 1\n")
    assert main(["run", path]) == 1
    assert "no flow section" in capsys.readouterr().err


def test_ls_and_search(capsys):
    assert main(["ls", "/stat"]) == 0
    out = capsys.readouterr().out
    assert "/stat/proj/mean" in out
    assert "/series" not in out
    assert main(["search", "rolling"]) == 0
    assert "/series/statlib/rolling_mean" in capsys.readouterr().out


def test_ls_with_recipe_plugins(write, tmp_path, monkeypatch, capsys):
    module = tmp_path / "ls_probe_mod.py"
    module.write_text(
        "import cirak\n\n"
        "def helper(x):\n"
        "    return x\n\n"
        "cirak.register('/num/lsdemo/helper', helper, description='from plugin')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("ls_probe_mod", None)
    recipe = write("a.yaml", "plugins: [ls_probe_mod]\n")
    assert main(["ls", "/num", "--recipe", recipe]) == 0
    assert "/num/lsdemo/helper" in capsys.readouterr().out
    sys.modules.pop("ls_probe_mod", None)


def test_ls_with_broken_recipe_still_lists(tmp_path, capsys):
    missing = str(tmp_path / "yok.yaml")
    assert main(["ls", "/stat", "--recipe", missing]) == 0
    captured = capsys.readouterr()
    assert "include_not_found" in captured.err
    assert "/stat/proj/mean" in captured.out


def test_no_escape_codes_when_piped(write, capsys):
    path = write("a.yaml", "c: {uri: nope}\n")
    main(["check", path])
    assert "\x1b[" not in capsys.readouterr().out


def test_style_paints_only_when_enabled():
    from cirak.cli import Style
    assert Style(True).red("x") == "\x1b[31mx\x1b[0m"
    assert Style(False).red("x") == "x"


def test_no_color_env(monkeypatch):
    from cirak.cli import _style_for

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setenv("NO_COLOR", "1")
    assert _style_for(Tty()).enabled is False
    monkeypatch.delenv("NO_COLOR")
    assert _style_for(Tty()).enabled is True


def test_usage_error_exit_2():
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def test_broken_entry_point_warns(monkeypatch, capsys):
    class Broken:
        name = "boom"

        def load(self):
            raise RuntimeError("no")

    monkeypatch.setattr("cirak.cli.entry_points", lambda group: [Broken()])
    assert main(["ls", "/"]) == 0
    assert "cannot load plugin boom" in capsys.readouterr().err
