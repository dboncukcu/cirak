import re

from .errors import Problem, dotted, error

TOKEN = re.compile(r"\$\$|\$([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\$")
FOREACH_NAMES = ("item", "index", "count", "key")
URI_FIELDS = ("uri", "builder", "block")
CONDITION_FIELDS = ("decide",)
SKIPPED_SECTIONS = ("alias", "params", "plugins")


def resolve(data: dict, provenance: dict) -> tuple[dict, list[Problem]]:
    problems: list[Problem] = []
    aliases = _alias_table(data, provenance, problems)
    globals_ = data.get("params") if isinstance(data.get("params"), dict) else {}
    result = {}
    for key, value in data.items():
        if key in SKIPPED_SECTIONS:
            result[key] = value
        else:
            local = foreach_names(value) if key == "flow" else frozenset()
            result[key] = _walk(value, (key,), aliases, globals_, provenance, problems, local, False)
    return result, problems


def foreach_names(value) -> frozenset:
    """The names every foreach under ``value`` introduces: its item, index, count and key."""
    found = set()
    if isinstance(value, dict):
        spec = value.get("foreach")
        if isinstance(spec, dict):
            found.add(spec.get("item", "item") if isinstance(spec.get("item", "item"), str) else "item")
            for field in ("index", "count"):
                if isinstance(spec.get(field), str):
                    found.add(spec[field])
        for item in value.values():
            found |= foreach_names(item)
    elif isinstance(value, list):
        for item in value:
            found |= foreach_names(item)
    return frozenset(found)


def _alias_table(data, provenance, problems) -> dict[str, str]:
    table = data.get("alias", {})
    if not isinstance(table, dict):
        problems.append(error("parse_error", "alias must be a mapping of names to URIs",
                              provenance.get(("alias",))))
        return {}
    aliases = {}
    for name, target in table.items():
        source = provenance.get(("alias", name))
        if not isinstance(name, str) or "/" in name:
            problems.append(error("parse_error", f"invalid alias name {name!r}", source))
        elif not isinstance(target, str):
            problems.append(error("parse_error", f"alias {name!r} must map to a string", source))
        else:
            aliases[name] = target
    return aliases


def _walk(value, path, aliases, globals_, provenance, problems, block_vars, in_params):
    if isinstance(value, dict):
        if len(path) == 2 and path[0] == "blocks":
            declared = value.get("variables")
            block_vars = foreach_names(value.get("flow"))
            if isinstance(declared, dict):
                block_vars |= frozenset(str(name) for name in declared)
        result = {}
        for key, item in value.items():
            child = path + (key,)
            uri_field = key == "uri" or (not in_params and key in URI_FIELDS)
            condition_field = not in_params and path[0] == "flow" and key in CONDITION_FIELDS
            if (uri_field or condition_field) and isinstance(item, str):
                result[key] = _uri_value(item, key, child, aliases, provenance, problems, block_vars)
            elif key == "params" and isinstance(item, dict):
                result[key] = _walk(item, child, aliases, globals_, provenance, problems, block_vars, True)
            else:
                result[key] = _walk(item, child, aliases, globals_, provenance, problems, block_vars, in_params)
        return result
    if isinstance(value, list):
        return [_walk(item, path + (index,), aliases, globals_, provenance, problems, block_vars, in_params)
                for index, item in enumerate(value)]
    if isinstance(value, str):
        return _substitute(value, path, block_vars, globals_, provenance, problems)
    return value


def _uri_value(text, key, path, aliases, provenance, problems, block_vars=frozenset()) -> str:
    if "$" in text or text.startswith("@"):
        whole = TOKEN.fullmatch(text)
        if whole and whole.group(1) and whole.group(1).split(".")[0] in block_vars:
            return text
        problems.append(error("forbidden_placeholder",
                              f"{key} must be a literal value at {dotted(path)}, got {text!r}",
                              provenance.get(path),
                              hint="uri, block and builder fields stay statically resolvable; "
                                   "inside a block a block variable is allowed"))
        return text
    if key == "block" or text.startswith("/"):
        return text
    seen: list[str] = []
    current = text
    while True:
        if current in seen:
            chain = " -> ".join([*seen, current])
            problems.append(error("alias_cycle", f"alias cycle: {chain}", provenance.get(path)))
            return text
        seen.append(current)
        if current.startswith("/"):
            return current
        if current not in aliases:
            problems.append(error("unknown_alias",
                                  f"{current!r} is not a known alias and does not start with /",
                                  provenance.get(path),
                                  hint="define it in the alias section or write a full URI"))
            return text
        current = aliases[current]


def _substitute(text, path, block_vars, globals_, provenance, problems):
    if "$" not in text:
        return text

    def lookup(name):
        base, _, field = name.partition(".")
        if base in block_vars:
            return True, None
        if base not in globals_:
            problems.append(error("unknown_variable", f"unknown variable ${name}$ at {dotted(path)}",
                                  provenance.get(path)))
            return True, None
        if not field:
            return False, globals_[base]
        holder = globals_[base]
        if not isinstance(holder, dict) or field not in holder:
            problems.append(error("unknown_variable",
                                  f"param {base!r} has no field {field!r} for ${name}$ at {dotted(path)}",
                                  provenance.get(path)))
            return True, None
        return False, holder[field]

    whole = TOKEN.fullmatch(text)
    if whole and whole.group(1):
        keep, value = lookup(whole.group(1))
        return text if keep else value

    def piece(match):
        if match.group(1) is None:
            return "$"
        keep, value = lookup(match.group(1))
        return match.group(0) if keep else str(value)

    return TOKEN.sub(piece, text)


class FillError(Exception):
    pass


def _field(name, values):
    """The value behind ``$name$`` or ``$name.field$``; None with False when the name is not ours."""
    base, _, field = name.partition(".")
    if base not in values:
        return False, None
    if not field:
        return True, values[base]
    holder = values[base]
    if not isinstance(holder, dict):
        raise FillError(f"${name}$ reads a field of {base!r}, which is not a mapping")
    if field not in holder:
        raise FillError(f"${name}$ reads a field {base!r} does not have")
    return True, holder[field]


def fill(value, values: dict):
    """Substitute block variables; names not in ``values`` are left for a later pass."""
    if isinstance(value, dict):
        return {fill(key, values) if isinstance(key, str) else key: fill(item, values)
                for key, item in value.items()}
    if isinstance(value, list):
        return [fill(item, values) for item in value]
    if isinstance(value, str) and "$" in value:
        whole = TOKEN.fullmatch(value)
        if whole and whole.group(1):
            known, found = _field(whole.group(1), values)
            return found if known else value

        def piece(match):
            if match.group(1) is None:
                return "$"
            known, found = _field(match.group(1), values)
            return str(found) if known else match.group(0)

        return TOKEN.sub(piece, value)
    return value
