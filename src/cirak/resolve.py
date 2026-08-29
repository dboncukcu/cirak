import re

from .errors import Problem, dotted, error

TOKEN = re.compile(r"\$\$|\$([A-Za-z_][A-Za-z0-9_]*)\$")
URI_FIELDS = ("uri", "builder", "block")
CONDITION_FIELDS = ("when", "until", "decide")
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
            result[key] = _walk(value, (key,), aliases, globals_, provenance, problems, frozenset(), False)
    return result, problems


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
            if isinstance(declared, dict):
                block_vars = frozenset(str(name) for name in declared)
        result = {}
        for key, item in value.items():
            child = path + (key,)
            uri_field = key in URI_FIELDS
            condition_field = path[0] == "flow" and key in CONDITION_FIELDS
            if not in_params and (uri_field or condition_field) and isinstance(item, str):
                result[key] = _uri_value(item, key, child, aliases, provenance, problems)
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


def _uri_value(text, key, path, aliases, provenance, problems) -> str:
    if "$" in text or text.startswith("@"):
        problems.append(error("forbidden_placeholder",
                              f"{key} must be a literal value at {dotted(path)}, got {text!r}",
                              provenance.get(path),
                              hint="uri, block and builder fields stay statically resolvable"))
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
    whole = TOKEN.fullmatch(text)
    if whole and whole.group(1):
        name = whole.group(1)
        if name in block_vars:
            return text
        if name in globals_:
            return globals_[name]
        problems.append(error("unknown_variable", f"unknown variable ${name}$ at {dotted(path)}",
                              provenance.get(path)))
        return text

    def piece(match):
        if match.group(1) is None:
            return "$"
        name = match.group(1)
        if name in block_vars:
            return match.group(0)
        if name in globals_:
            return str(globals_[name])
        problems.append(error("unknown_variable", f"unknown variable ${name}$ at {dotted(path)}",
                              provenance.get(path)))
        return match.group(0)

    return TOKEN.sub(piece, text)


def fill(value, values: dict):
    if isinstance(value, dict):
        return {key: fill(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [fill(item, values) for item in value]
    if isinstance(value, str) and "$" in value:
        whole = TOKEN.fullmatch(value)
        if whole and whole.group(1):
            name = whole.group(1)
            return values[name] if name in values else value
        return TOKEN.sub(
            lambda match: "$" if match.group(1) is None
            else str(values.get(match.group(1), match.group(0))),
            value)
    return value
