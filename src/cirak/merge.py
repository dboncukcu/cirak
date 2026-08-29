from .errors import Problem, Source, dotted, error
from .loader import LoadedFile

POOLED_PATHS = {("plugins",)}


def merge(files: list[LoadedFile]) -> tuple[dict, dict[tuple, Source], list[Problem]]:
    data: dict = {}
    provenance: dict[tuple, Source] = {}
    problems: list[Problem] = []
    for loaded in files:
        _merge_maps(data, loaded.data, (), provenance, loaded.provenance, problems)
    return data, provenance, problems


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
        if isinstance(existing, dict) and isinstance(value, dict):
            _merge_maps(existing, value, child, provenance, incoming_provenance, problems)
            continue
        first = provenance.get(child)
        second = incoming_provenance.get(child)
        if isinstance(existing, dict) or isinstance(value, dict):
            problems.append(error(
                "type_mismatch",
                f"{dotted(child)} is a mapping in one file and a plain value in another: {first} and {second}"))
            continue
        problems.append(error(
            "merge_conflict",
            f"{dotted(child)} is defined twice: {first} and {second}",
            hint="strict merging never overrides; keep exactly one definition and select by file"))


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
