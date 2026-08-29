import argparse
import os
import sys
from importlib.metadata import entry_points

from ruamel.yaml import YAML
from tezgah import TezgahError

from .api import analyze, check, load_plugins
from .api import run as run_recipe
from .errors import CirakError, ConfigError
from .registry import registry


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def bold(self, text: str) -> str:
        return self._paint(text, "1")

    def dim(self, text: str) -> str:
        return self._paint(text, "2")

    def red(self, text: str) -> str:
        return self._paint(text, "31")

    def green(self, text: str) -> str:
        return self._paint(text, "32")

    def yellow(self, text: str) -> str:
        return self._paint(text, "33")

    def cyan(self, text: str) -> str:
        return self._paint(text, "36")

    def _paint(self, text: str, code: str) -> str:
        if not self.enabled:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"


def _style_for(stream) -> Style:
    is_tty = stream.isatty() if hasattr(stream, "isatty") else False
    return Style(is_tty and "NO_COLOR" not in os.environ)


def main(argv=None) -> int:
    _load_entry_point_plugins()
    args = _parser().parse_args(argv)
    return args.handler(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cirak",
        description="Compile YAML recipes into Python objects and tezgah pipelines")
    commands = parser.add_subparsers(dest="command", required=True)

    check_cmd = commands.add_parser("check", help="compile and report every problem")
    check_cmd.add_argument("paths", nargs="+")
    check_cmd.set_defaults(handler=_cmd_check)

    show_cmd = commands.add_parser("show", help="print the resolved recipe")
    show_cmd.add_argument("paths", nargs="+")
    show_cmd.add_argument("--expanded", action="store_true",
                          help="also print expanded block graphs")
    show_cmd.set_defaults(handler=_cmd_show)

    run_cmd = commands.add_parser("run", help="compile and run on tezgah")
    run_cmd.add_argument("paths", nargs="+")
    run_cmd.add_argument("--record", help="directory for resolved.yaml and tezgah records")
    run_cmd.add_argument("--executor", default="serial")
    run_cmd.add_argument("--workers", type=int)
    run_cmd.set_defaults(handler=_cmd_run)

    ls_cmd = commands.add_parser("ls", help="list registry entries under a path")
    ls_cmd.add_argument("prefix", nargs="?", default="/")
    ls_cmd.add_argument("--recipe", action="append", default=[], metavar="PATH",
                        help="load this recipe's plugins before listing")
    ls_cmd.set_defaults(handler=_cmd_ls)

    search_cmd = commands.add_parser("search", help="search uris and descriptions")
    search_cmd.add_argument("term")
    search_cmd.add_argument("--recipe", action="append", default=[], metavar="PATH",
                            help="load this recipe's plugins before searching")
    search_cmd.set_defaults(handler=_cmd_search)

    return parser


def _cmd_check(args) -> int:
    problems = check(args.paths)
    if not problems:
        style = _style_for(sys.stdout)
        print(style.green("no problems found"))
        return 0
    _print_problems(problems, sys.stdout)
    return 1 if any(problem.severity == "error" for problem in problems) else 0


def _cmd_show(args) -> int:
    analysis = analyze(args.paths)
    failures = [problem for problem in analysis.problems if problem.severity == "error"]
    if failures:
        _print_problems(failures, sys.stderr)
        return 1
    yaml = YAML()
    yaml.dump(analysis.data, sys.stdout)
    if args.expanded and analysis.expansions:
        sys.stdout.write("---\n")
        yaml.dump({"expanded": analysis.expansions}, sys.stdout)
    return 0


def _cmd_run(args) -> int:
    try:
        report = run_recipe(args.paths, record_dir=args.record,
                            executor=args.executor, workers=args.workers)
    except ConfigError as exc:
        _print_problems(exc.problems, sys.stderr)
        return 1
    except (CirakError, TezgahError) as exc:
        style = _style_for(sys.stderr)
        print(style.red(str(exc)), file=sys.stderr)
        return 1
    style = _style_for(sys.stdout)
    print(f"run {style.bold(report.run)}: {style.green('ok')}")
    if report.outputs:
        print("outputs:")
        for key, value in report.outputs.items():
            print(f"  {style.cyan(key)}: {value!r}")
    return 0


def _cmd_ls(args) -> int:
    _load_recipe_plugins(args.recipe)
    _print_entries(registry.ls(args.prefix))
    return 0


def _cmd_search(args) -> int:
    _load_recipe_plugins(args.recipe)
    _print_entries(registry.search(args.term))
    return 0


def _print_problems(problems, stream) -> None:
    style = _style_for(stream)
    word = "problem" if len(problems) == 1 else "problems"
    print(style.bold(f"{len(problems)} {word} found:"), file=stream)
    for number, problem in enumerate(problems, 1):
        paint = style.red if problem.severity == "error" else style.yellow
        line = f"  {number}. {paint(f'[{problem.kind}]')} {problem.message}"
        if problem.file is not None:
            line += " " + style.cyan(f"({problem.file}:{problem.line})")
        if problem.hint is not None:
            line += style.dim(f"; {problem.hint}")
        print(line, file=stream)


def _print_entries(entries) -> None:
    style = _style_for(sys.stdout)
    if not entries:
        print(style.dim("nothing found"))
        return
    width = max(len(entry.uri) for entry in entries)
    for entry in entries:
        print(f"{style.cyan(entry.uri.ljust(width))}  {style.dim(entry.description)}")


def _load_recipe_plugins(paths) -> None:
    if not paths:
        return
    problems = load_plugins(paths)
    if problems:
        _print_problems(problems, sys.stderr)


def _load_entry_point_plugins() -> None:
    for entry in entry_points(group="cirak.plugins"):
        try:
            entry.load()
        except Exception as exc:
            style = _style_for(sys.stderr)
            print(style.yellow(f"warning: cannot load plugin {entry.name}: {exc}"),
                  file=sys.stderr)
