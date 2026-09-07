from dataclasses import dataclass, field
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


@dataclass
class Layer:
    """One layer of a recipe: its files, merged strictly, above the layers it includes.

    The root layer holds the files given on the command line; every included
    file is a layer of its own, placed below the file that includes it, in
    list order from bottom to top.
    """

    files: list[LoadedFile] = field(default_factory=list)
    below: list["Layer"] = field(default_factory=list)
    label: str = ""
    included_by: str | None = None

    def walk(self):
        for layer in self.below:
            yield from layer.walk()
        yield from self.files

    def ordered(self, depth=0):
        """Layers bottom to top as (depth, layer) pairs."""
        for layer in self.below:
            yield from layer.ordered(depth + 1)
        yield depth, self


def load(paths, fragments=None) -> tuple[Layer, list[Problem]]:
    problems: list[Problem] = []
    done: set[Path] = set()
    root = Layer(label="command line")
    for raw in paths:
        _load_into(root, Path(raw).resolve(), problems, done, [], fragments, top=True)
    return root, problems


def _load_into(parent, path, problems, done, stack, fragments, top=False) -> None:
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
    provenance: dict[tuple, Source] = {}
    data = _convert(tree, (), str(path), provenance)
    includes = data.pop("include", [])
    if not isinstance(includes, list):
        problems.append(error("parse_error", "include must be a list of paths or fragment URIs",
                              provenance.get(("include",))))
        includes = []
    loaded = LoadedFile(str(path), data, provenance)
    if top:
        layer = parent
        layer.files.append(loaded)
    else:
        layer = Layer(files=[loaded], label=str(path), included_by=str(stack[-1]) if stack else None)
        parent.below.append(layer)
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
                _load_into(layer, Path(target).resolve(), problems, done, stack, fragments)
        else:
            _load_into(layer, (path.parent / entry).resolve(), problems, done, stack, fragments)
    stack.pop()


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


def parse_value(text: str):
    """Parse a command line value the way YAML would read it in a file."""
    yaml = YAML(typ="safe")
    loaded = yaml.load(text)
    return _plain(loaded)


def _plain(value):
    if isinstance(value, dict):
        return {_scalar(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return _scalar(value)


def set_layer(sets) -> Layer:
    """The top layer built from ``--set path=value`` assignments."""
    data: dict = {}
    provenance: dict[tuple, Source] = {}
    for number, (dotted_path, value) in enumerate(sets, 1):
        parts = tuple(dotted_path.split("."))
        target = data
        for depth, part in enumerate(parts[:-1]):
            if not isinstance(target.get(part), dict):
                target[part] = {}
            provenance.setdefault(parts[:depth + 1], Source("--set", number))
            target = target[part]
        target[parts[-1]] = value
        _stamp(parts, value, provenance, Source("--set", number))
    return Layer(files=[LoadedFile("--set", data, provenance)], label="--set")


def _stamp(path, value, provenance, source) -> None:
    provenance[path] = source
    if isinstance(value, dict):
        for key, item in value.items():
            _stamp(path + (key,), item, provenance, source)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _stamp(path + (index,), item, provenance, source)
