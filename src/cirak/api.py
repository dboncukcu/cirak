import re
import sys
import warnings
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from ruamel.yaml import YAML
from tezgah import ValidationError as TezgahValidationError
from tezgah import run as tezgah_run

from .build import build_components
from .errors import (
    CirakError,
    CirakWarning,
    ConfigError,
    Problem,
    error,
    render_problems,
)
from .expand import expand
from .flow import compile_flow
from .loader import load
from .merge import merge
from .registry import registry as global_registry
from .resolve import resolve as resolve_values
from .validate import validate


@dataclass(frozen=True)
class Analysis:
    data: dict
    provenance: dict
    expansions: dict
    problems: list[Problem]


def analyze(paths) -> Analysis:
    files, load_problems = load(paths, global_registry.fragments())
    data, provenance, merge_problems = merge(files)
    _extend_sys_path(files)
    imported_new, plugin_problems = _import_plugins(data.get("plugins", []))
    if imported_new:
        files, load_problems = load(paths, global_registry.fragments())
        data, provenance, merge_problems = merge(files)
    data, resolve_problems = resolve_values(data, provenance)
    expansions, expand_problems = expand(data, provenance)
    collected = [*load_problems, *merge_problems, *plugin_problems,
                 *resolve_problems, *expand_problems]
    collected += validate(data, provenance, expansions, global_registry)
    return Analysis(data, provenance, expansions, collected)


def check(paths) -> list[Problem]:
    return analyze(paths).problems


def resolve(paths) -> dict:
    analysis = analyze(paths)
    gate(analysis.problems)
    return analysis.data


def load_plugins(paths) -> list[Problem]:
    files, load_problems = load(paths, global_registry.fragments())
    data, _, merge_problems = merge(files)
    _extend_sys_path(files)
    _, plugin_problems = _import_plugins(data.get("plugins", []))
    return [*load_problems, *merge_problems, *plugin_problems]


def run(paths, *, record_dir=None, executor="serial", workers=None):
    analysis = analyze(paths)
    gate(analysis.problems)
    if not isinstance(analysis.data.get("flow"), dict):
        raise CirakError("recipe has no flow section to run")
    store = build_components(analysis.data, analysis.expansions, global_registry)
    pipeline = compile_flow(analysis.data, store, global_registry)
    try:
        pipeline.validate()
    except TezgahValidationError as exc:
        raise ConfigError(_tezgah_problems(exc, analysis.provenance)) from exc
    if record_dir is not None:
        _write_resolved(analysis.data, record_dir)
    return tezgah_run(pipeline, inputs=None, executor=executor, workers=workers,
                      record_dir=record_dir)


def gate(problems) -> None:
    failures = [problem for problem in problems if problem.severity == "error"]
    warns = [problem for problem in problems if problem.severity == "warning"]
    if warns:
        warnings.warn(CirakWarning(render_problems(warns)), stacklevel=2)
    if failures:
        raise ConfigError(failures)


def _import_plugins(names) -> tuple[bool, list[Problem]]:
    problems: list[Problem] = []
    imported_new = False
    if not isinstance(names, list):
        problems.append(error("parse_error", "plugins must be a list of module names"))
        return imported_new, problems
    for name in names:
        if not isinstance(name, str):
            problems.append(error("plugin_import_failed",
                                  f"plugin entries must be strings, got {name!r}"))
        elif name not in sys.modules:
            try:
                import_module(name)
                imported_new = True
            except Exception as exc:
                problems.append(error("plugin_import_failed",
                                      f"cannot import plugin {name}: {exc}"))
    return imported_new, problems


def _extend_sys_path(files) -> None:
    for loaded in files:
        directory = str(Path(loaded.file).parent)
        if directory not in sys.path:
            sys.path.insert(0, directory)


def _tezgah_problems(exc, provenance) -> list[Problem]:
    texts = [str(item) for item in getattr(exc, "problems", None) or [exc]]
    return [error("tezgah_validation", text, _flow_source(text, provenance))
            for text in texts]


def _flow_source(text, provenance):
    match = re.search(r"'([^']+)'", text)
    if match is None:
        return None
    name = match.group(1)
    for path, source in provenance.items():
        if path and path[0] == "flow" and path[-1] == name:
            return source
    return None


def _write_resolved(data, record_dir) -> None:
    target = Path(record_dir)
    target.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    with (target / "resolved.yaml").open("w") as stream:
        yaml.dump(data, stream)
