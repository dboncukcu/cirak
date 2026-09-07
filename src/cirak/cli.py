import argparse
import os
import sys
from importlib.metadata import entry_points

from ruamel.yaml import YAML
from tezgah import TezgahError

from .api import analyze, check, flow_dump, layers, load_plugins
from .api import run as run_recipe
from .errors import CirakError, ConfigError
from .loader import parse_value
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
    _recipe_options(check_cmd)
    check_cmd.add_argument("--input", action="append", default=[], metavar="NAME",
                           help="a root bus key run will be given; repeatable")
    check_cmd.add_argument("--layers", action="store_true",
                           help="print the layer tree and the leaves each file overrides")
    check_cmd.set_defaults(handler=_cmd_check)

    show_cmd = commands.add_parser("show", help="print the resolved recipe")
    show_cmd.add_argument("paths", nargs="+")
    _recipe_options(show_cmd)
    show_cmd.add_argument("--expanded", action="store_true",
                          help="also print expanded block graphs")
    show_cmd.add_argument("--flow", action="store_true",
                          help="print the expanded flow instead, annotated with what tezgah resolved")
    show_cmd.add_argument("--input", action="append", default=[], metavar="NAME",
                          help="a root bus key run will be given (with --flow); repeatable")
    show_cmd.set_defaults(handler=_cmd_show)

    run_cmd = commands.add_parser("run", help="compile and run on tezgah")
    run_cmd.add_argument("paths", nargs="+")
    _recipe_options(run_cmd)
    run_cmd.add_argument("--input", action="append", default=[], metavar="NAME=VALUE",
                         help="a root bus key and its YAML value; repeatable")
    run_cmd.add_argument("--record", help="directory for resolved.yaml, flow.yaml and tezgah records")
    run_cmd.add_argument("--executor", default="serial")
    run_cmd.add_argument("--workers", type=int)
    run_cmd.set_defaults(handler=_cmd_run)

    ls_cmd = commands.add_parser("ls", help="list registry entries under a path")
    ls_cmd.add_argument("prefix", nargs="?", default="/")
    ls_cmd.add_argument("--recipe", action="append", default=[], metavar="PATH",
                        help="load this recipe's plugins before listing")
    ls_cmd.add_argument("--kind", help="only legos of this kind")
    ls_cmd.set_defaults(handler=_cmd_ls)

    search_cmd = commands.add_parser("search", help="search uris and descriptions")
    search_cmd.add_argument("term")
    search_cmd.add_argument("--recipe", action="append", default=[], metavar="PATH",
                            help="load this recipe's plugins before searching")
    search_cmd.set_defaults(handler=_cmd_search)

    return parser


def _recipe_options(command) -> None:
    command.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                         help="override a leaf as the top layer; the value is read as YAML; repeatable")


def _sets(args):
    found = []
    for text in args.set:
        path, separator, value = text.partition("=")
        if not separator or not path:
            raise SystemExit(_usage(f"--set expects PATH=VALUE, got {text!r}"))
        found.append((path, parse_value(value)))
    return found


def _input_names(args):
    return [text.partition("=")[0] for text in args.input]


def _input_values(args):
    found = {}
    for text in args.input:
        name, separator, value = text.partition("=")
        if not separator or not name:
            raise SystemExit(_usage(f"--input expects NAME=VALUE, got {text!r}"))
        found[name] = parse_value(value)
    return found


def _usage(message) -> int:
    print(f"cirak: error: {message}", file=sys.stderr)
    return 2


def _cmd_check(args) -> int:
    if args.layers:
        print(layers(args.paths, sets=_sets(args)))
    problems = check(args.paths, sets=_sets(args), inputs=_input_names(args))
    if not problems:
        style = _style_for(sys.stdout)
        print(style.green("no problems found"))
        return 0
    _print_problems(problems, sys.stdout)
    return 1 if any(problem.severity == "error" for problem in problems) else 0


def _cmd_show(args) -> int:
    if args.flow:
        try:
            sys.stdout.write(flow_dump(args.paths, sets=_sets(args), inputs=_input_names(args)))
        except ConfigError as exc:
            _print_problems(exc.problems, sys.stderr)
            return 1
        except CirakError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    analysis = analyze(args.paths, _sets(args))
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
        report = run_recipe(args.paths, sets=_sets(args), inputs=_input_values(args), record_dir=args.record,
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
    _print_entries(registry.ls(args.prefix, kind=args.kind))
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
    kinds = [entry.facts.kind or ("fragment" if entry.fragment else "") for entry in entries]
    kind_width = max(len(kind) for kind in kinds)
    for entry, kind in zip(entries, kinds):
        line = f"{style.cyan(entry.uri.ljust(width))}  {style.yellow(kind.ljust(kind_width))}  {style.dim(entry.description)}"
        facts = {name: value for name, value in entry.facts.declared().items() if name != "kind"}
        if facts:
            line += "  " + style.dim(_facts_text(facts))
        print(line)


def _facts_text(facts) -> str:
    parts = []
    for name, value in facts.items():
        if isinstance(value, list):
            parts.append(f"{name}: {', '.join(str(item) for item in value)}")
        elif isinstance(value, dict):
            parts.append(f"{name}: " + ", ".join(f"{key}={item}" for key, item in value.items()))
        else:
            parts.append(f"{name}: {value}")
    return "[" + "; ".join(parts) + "]"


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
