from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.scalarbool import ScalarBoolean

from .errors import Problem, Source, error


@dataclass(frozen=True)
class LoadedFile:
    file: str
    data: dict
    provenance: dict[tuple, Source]


def load(paths, fragments=None) -> tuple[list[LoadedFile], list[Problem]]:
    files: list[LoadedFile] = []
    problems: list[Problem] = []
    done: set[Path] = set()
    stack: list[Path] = []
    for raw in paths:
        _load_file(Path(raw).resolve(), files, problems, done, stack, fragments)
    return files, problems


def _load_file(path, files, problems, done, stack, fragments) -> None:
    if path in stack:
        chain = " -> ".join(entry.name for entry in [*stack, path])
        problems.append(error("include_cycle", f"include cycle: {chain}"))
        return
    if path in done:
        return
    done.add(path)
    tree, parse_problems = _parse(path)
    problems.extend(parse_problems)
    if tree is None:
        return
    data: dict = {}
    provenance: dict[tuple, Source] = {}
    converted = _convert(tree, (), str(path), provenance)
    data.update(converted)
    includes = data.pop("include", [])
    if not isinstance(includes, list):
        problems.append(error("parse_error", "include must be a list of paths or fragment URIs",
                              provenance.get(("include",))))
        includes = []
    stack.append(path)
    for position, entry in enumerate(includes):
        source = provenance.get(("include", position))
        if not isinstance(entry, str):
            problems.append(error("parse_error", "include entries must be strings", source))
        elif entry.startswith("/"):
            target = None if fragments is None else fragments.get(entry)
            if target is None:
                problems.append(error("include_not_found", f"fragment {entry} is not registered", source,
                                      hint="fragment URIs resolve through the registry"))
            else:
                _load_file(Path(target).resolve(), files, problems, done, stack, fragments)
        else:
            _load_file((path.parent / entry).resolve(), files, problems, done, stack, fragments)
    stack.pop()
    files.append(LoadedFile(str(path), data, provenance))


def _parse(path):
    try:
        text = path.read_text()
    except OSError as exc:
        return None, [error("include_not_found", f"cannot read {path}: {exc.strerror or exc}")]
    yaml = YAML()
    yaml.allow_duplicate_keys = False
    try:
        tree = yaml.load(text)
    except DuplicateKeyError as exc:
        return None, [error("duplicate_key", _message(exc), _mark(exc, path))]
    except MarkedYAMLError as exc:
        return None, [error("parse_error", _message(exc), _mark(exc, path))]
    except YAMLError as exc:
        return None, [error("parse_error", str(exc), Source(str(path), 1))]
    if tree is None:
        return {}, []
    if not isinstance(tree, dict):
        return None, [error("parse_error", "top level must be a mapping", Source(str(path), 1))]
    return tree, []


def _message(exc) -> str:
    problem = getattr(exc, "problem", None)
    return str(problem).strip() if problem else str(exc)


def _mark(exc, path) -> Source:
    mark = getattr(exc, "problem_mark", None)
    return Source(str(path), mark.line + 1 if mark else 1)


def _convert(node, path, file, provenance):
    if isinstance(node, CommentedMap):
        result = {}
        for key, value in node.items():
            plain_key = _scalar(key)
            child = path + (plain_key,)
            mark = node.lc.data.get(key)
            provenance[child] = Source(file, (mark[0] if mark else 0) + 1)
            result[plain_key] = _convert(value, child, file, provenance)
        return result
    if isinstance(node, CommentedSeq):
        result = []
        for index, value in enumerate(node):
            child = path + (index,)
            mark = node.lc.data.get(index)
            provenance[child] = Source(file, (mark[0] if mark else 0) + 1)
            result.append(_convert(value, child, file, provenance))
        return result
    if isinstance(node, dict):
        return {_scalar(key): _convert(value, path + (_scalar(key),), file, provenance)
                for key, value in node.items()}
    if isinstance(node, list):
        return [_convert(value, path + (index,), file, provenance)
                for index, value in enumerate(node)]
    return _scalar(node)


def _scalar(value):
    if isinstance(value, ScalarBoolean):
        return bool(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value
