from .errors import Problem, Source, dotted, error
from .loader import Layer, LoadedFile

POOLED_PATHS = {("plugins",)}


def merge(files: list[LoadedFile]) -> tuple[dict, dict[tuple, Source], list[Problem]]:
    """Strict merge of the files of one layer: the same leaf twice is a conflict."""
    data: dict = {}
    provenance: dict[tuple, Source] = {}
    problems: list[Problem] = []
    for loaded in files:
        _merge_maps(data, loaded.data, (), provenance, loaded.provenance, problems)
    return data, provenance, problems


def is_record(value) -> bool:
    """A lego call: a mapping with ``uri`` or ``block``.

    Written in an upper layer it replaces whatever the lower layers had under
    that key; a partial mapping without ``uri`` or ``block`` merges into the
    existing call instead (``--set data.split.params.val=null``).
    """
    return isinstance(value, dict) and ("uri" in value or "block" in value)


def merge_layers(layer: Layer):
    """Fold a layer tree into one recipe.

    Layers below are folded first, bottom to top, and an upper layer overrides
    the leaves of the layers under it: mappings merge recursively, lists and
    scalars are replaced whole, a lego record (a mapping with ``uri`` or
    ``block``) written above replaces the whole value below it, ``plugins`` are
    pooled. Inside a layer the merge is strict. Returns the data, the provenance
    of every value, the record of overridden leaves as (path, winner, loser)
    triples and the problems.
    """
    data: dict = {}
    provenance: dict[tuple, Source] = {}
    overrides: list[tuple[tuple, Source, Source]] = []
    problems: list[Problem] = []
    for lower in layer.below:
        lower_data, lower_provenance, lower_overrides, lower_problems = merge_layers(lower)
        problems.extend(lower_problems)
        overrides.extend(lower_overrides)
        _override(data, lower_data, (), provenance, lower_provenance, overrides)
    own_data, own_provenance, own_problems = merge(layer.files)
    problems.extend(own_problems)
    _override(data, own_data, (), provenance, own_provenance, overrides)
    return data, provenance, overrides, problems


def _override(target, incoming, path, provenance, incoming_provenance, overrides) -> None:
    for key, value in incoming.items():
        child = path + (key,)
        if key not in target:
            target[key] = _copy(value)
            _adopt(child, value, provenance, incoming_provenance)
            continue
        existing = target[key]
        if child in POOLED_PATHS and isinstance(existing, list) and isinstance(value, list):
            for item in value:
                if item not in existing:
                    existing.append(item)
            continue
        if isinstance(existing, dict) and isinstance(value, dict) and not is_record(value):
            _override(existing, value, child, provenance, incoming_provenance, overrides)
            continue
        loser = provenance.get(child)
        winner = incoming_provenance.get(child)
        if loser is not None and winner is not None:
            overrides.append((child, winner, loser))
        _forget(child, provenance)
        target[key] = _copy(value)
        _adopt(child, value, provenance, incoming_provenance)


def _forget(prefix, provenance) -> None:
    for path in [path for path in provenance if path[:len(prefix)] == prefix]:
        del provenance[path]


def _merge_maps(target, incoming, path, provenance, incoming_provenance, problems) -> None:
    for key, value in incoming.items():
        child = path + (key,)
        if key not in target:
            target[key] = _copy(value)
            _adopt(child, value, provenance, incoming_provenance)
            continue
        existing = target[key]
        if child in POOLED_PATHS and isinstance(existing, list) and isinstance(value, list):
            for item in value:
                if item not in existing:
                    existing.append(item)
            continue
        if isinstance(existing, dict) and isinstance(value, dict) and not is_record(value):
            _merge_maps(existing, value, child, provenance, incoming_provenance, problems)
            continue
        first = provenance.get(child)
        second = incoming_provenance.get(child)
        if (isinstance(existing, dict) or isinstance(value, dict)) and not is_record(value):
            problems.append(error(
                "type_mismatch",
                f"{dotted(child)} is a mapping in one file and a plain value in another: {first} and {second}"))
            continue
        problems.append(error(
            "merge_conflict",
            f"{dotted(child)} is defined twice: {first} and {second}",
            hint="strict merging never overrides inside a layer; keep one definition or include the "
                 "other file as a layer"))


def _adopt(prefix, value, provenance, incoming_provenance) -> None:
    source = incoming_provenance.get(prefix)
    if source is not None:
        provenance[prefix] = source
    if isinstance(value, dict):
        for key, item in value.items():
            _adopt(prefix + (key,), item, provenance, incoming_provenance)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _adopt(prefix + (index,), item, provenance, incoming_provenance)


def _copy(value):
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def describe_layers(layer: Layer, overrides: list) -> str:
    """The layer tree bottom to top, with the leaves each file overrode."""
    lines = ["layers, bottom to top:"]
    number = 0
    for depth, entry in layer.ordered():
        for loaded in entry.files:
            number += 1
            origin = f" (included by {_short(entry.included_by)})" if entry.included_by else ""
            lines.append(f"  {number}. {_short(loaded.file)}{origin}")
            for path, winner, loser in overrides:
                if winner.file == loaded.file:
                    lines.append(f"       overrides {dotted(path)} ({loser})")
    return "\n".join(lines)


def _short(file) -> str:
    if file is None:
        return ""
    return file if file == "--set" else file.rsplit("/", 1)[-1]
