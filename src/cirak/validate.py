import difflib
import inspect

from .errors import Problem, dotted, error, warning
from .expand import components
from .resolve import TOKEN

PIPELINE_FIELDS = ("inputs", "outputs", "wait_for")
MARKERS = ("map", "loop", "branch")
STEP_ONLY_FIELDS = ("params", "when", "retries", "wait")


def validate(data: dict, provenance: dict, expansions: dict, registry=None) -> list[Problem]:
    problems: list[Problem] = []
    _check_components(data, provenance, problems)
    _check_blocks(data, provenance, problems)
    _check_graphs(data, provenance, expansions, problems)
    _check_references(data, provenance, problems)
    _check_flow(data, provenance, problems)
    if registry is not None:
        _check_uris(data, provenance, registry, problems)
        _check_signatures(data, provenance, expansions, registry, problems)
    return problems


def _check_components(data, provenance, problems) -> None:
    blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
    for path, component in components(data):
        source = provenance.get(path)
        name = dotted(path)
        has_uri = "uri" in component
        has_block = "block" in component
        if has_uri and has_block:
            problems.append(error("invalid_component", f"component {name} has both uri and block", source))
            continue
        if has_uri and not isinstance(component["uri"], str):
            problems.append(error("invalid_component", f"uri of component {name} must be a string", source))
        if has_block and not isinstance(component["block"], str):
            problems.append(error("invalid_component", f"block of component {name} must be a string", source))
        if "params" in component and not isinstance(component["params"], dict):
            problems.append(error("invalid_component", f"params of component {name} must be a mapping", source))
        if "partial" in component:
            if has_block:
                problems.append(error("invalid_component",
                                      f"partial is only valid with uri (component {name})", source))
            elif not isinstance(component["partial"], bool):
                problems.append(error("invalid_component",
                                      f"partial of component {name} must be a boolean", source))
        if "builder" in component and has_uri:
            problems.append(error("invalid_component",
                                  f"builder is only valid with block (component {name})", source))
        if has_block and isinstance(component["block"], str):
            definition = blocks.get(component["block"])
            definition = definition if isinstance(definition, dict) else {}
            builder = component.get("builder", definition.get("builder"))
            if not isinstance(builder, str):
                problems.append(error("missing_builder",
                                      f"component {name} needs a builder", source,
                                      hint="set builder on the component or on the block definition"))


def _check_blocks(data, provenance, problems) -> None:
    blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
    globals_ = data.get("params") if isinstance(data.get("params"), dict) else {}
    for name, definition in blocks.items():
        source = provenance.get(("blocks", name))
        if not isinstance(definition, dict):
            problems.append(error("invalid_block", f"block {name!r} must be a mapping", source))
            continue
        declared = definition.get("variables") if isinstance(definition.get("variables"), dict) else {}
        used: set[str] = set()
        _used_names(definition.get("spec"), used)
        _used_names(definition.get("graph"), used)
        for var in declared:
            if var in globals_:
                problems.append(error("shadowed_variable",
                                      f"variable {var!r} of block {name!r} shadows a global param",
                                      provenance.get(("blocks", name, "variables", var))))
            if var not in used:
                problems.append(warning("unused_variable",
                                        f"variable {var!r} of block {name!r} is never used",
                                        provenance.get(("blocks", name, "variables", var))))


def _used_names(value, used) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _used_names(item, used)
    elif isinstance(value, list):
        for item in value:
            _used_names(item, used)
    elif isinstance(value, str):
        for match in TOKEN.finditer(value):
            if match.group(1):
                used.add(match.group(1))


def _check_graphs(data, provenance, expansions, problems) -> None:
    paths = {dotted(path): path for path, _ in components(data)}
    for key, graph in expansions.items():
        source = provenance.get(paths.get(key, ()))
        inputs = set(graph["inputs"])
        producer: dict[str, str] = {}
        for node_name, node in graph["graph"].items():
            for wire in node["outputs"]:
                if wire in producer or wire in inputs:
                    problems.append(error("double_assignment",
                                          f"{wire!r} is produced more than once in {key}", source))
                else:
                    producer[wire] = node_name
        consumed = set(graph["outputs"])
        for node_name, node in graph["graph"].items():
            for wire in node["inputs"]:
                consumed.add(wire)
                if wire not in producer and wire not in inputs:
                    problems.append(error("unproduced_input",
                                          f"node {node_name!r} of {key} reads {wire!r} "
                                          f"but nothing produces it", source))
        for wire in graph["outputs"]:
            if wire not in producer and wire not in inputs:
                problems.append(error("missing_output",
                                      f"{key} declares output {wire!r} but nothing produces it", source))
        for wire, node_name in producer.items():
            if wire not in consumed:
                problems.append(warning("unused_output",
                                        f"output {wire!r} of node {node_name!r} in {key} "
                                        f"is never consumed", source))
        _graph_cycles(graph, producer, key, source, problems)


def _graph_cycles(graph, producer, key, source, problems) -> None:
    deps = {name: [producer[wire] for wire in node["inputs"] if wire in producer]
            for name, node in graph["graph"].items()}
    state: dict[str, str] = {}

    def visit(name, trail):
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            cycle = trail[trail.index(name):] + [name]
            problems.append(error("graph_cycle", f"cycle in {key}: {' -> '.join(cycle)}", source))
            state[name] = "done"
            return
        state[name] = "active"
        for dep in deps[name]:
            visit(dep, trail + [name])
        state[name] = "done"

    for name in deps:
        visit(name, [])


def _check_references(data, provenance, problems) -> None:
    found = components(data)
    component_keys = {dotted(path) for path, _ in found}
    groups: set[str] = set()
    for path, _ in found:
        for cut in range(1, len(path)):
            groups.add(dotted(path[:cut]))
    targets = component_keys | groups
    referenced: set[str] = set()
    edges: dict[str, set[str]] = {key: set() for key in component_keys}

    def register(ref, holder, ref_path):
        if ref not in targets:
            problems.append(error("unknown_reference",
                                  f"@{ref} does not match any component (at {dotted(ref_path)})",
                                  provenance.get(ref_path)))
            return
        members = [ref] if ref in component_keys else \
            [key for key in component_keys if key.startswith(ref + ".")]
        referenced.update(members)
        if holder is not None:
            edges[holder].update(members)

    for path, component in found:
        holder = dotted(path)
        refs = list(_refs(component.get("params"), path + ("params",)))
        if "block" in component:
            for _, ref_path in refs:
                problems.append(error("forbidden_placeholder",
                                      f"references are not allowed in block params (at {dotted(ref_path)})",
                                      provenance.get(ref_path)))
            continue
        for ref, ref_path in refs:
            register(ref, holder, ref_path)

    for ref, ref_path in _flow_refs(data.get("flow"), ("flow",)):
        register(ref, None, ref_path)

    state: dict[str, str] = {}

    def visit(key, trail):
        if state.get(key) == "done":
            return
        if state.get(key) == "active":
            cycle = trail[trail.index(key):] + [key]
            problems.append(error("reference_cycle",
                                  "reference cycle: " + " -> ".join(f"@{part}" for part in cycle)))
            state[key] = "done"
            return
        state[key] = "active"
        for dep in edges.get(key, ()):
            visit(dep, trail + [key])
        state[key] = "done"

    for key in component_keys:
        visit(key, [])

    for key in sorted(component_keys - referenced):
        problems.append(warning("unused_component", f"component {key} is never referenced"))


def _refs(value, path):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _refs(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _refs(item, path + (index,))
    elif isinstance(value, str) and value.startswith("@") and not value.startswith("@@"):
        yield value[1:], path


def _flow_refs(value, path):
    if isinstance(value, dict):
        for key, item in value.items():
            child = path + (key,)
            if key == "params":
                yield from _refs(item, child)
            else:
                yield from _flow_refs(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flow_refs(item, path + (index,))


def _check_flow(data, provenance, problems) -> None:
    flow = data.get("flow")
    if flow is None:
        return
    if not isinstance(flow, dict):
        problems.append(error("ambiguous_node", "flow must be a mapping of nodes",
                              provenance.get(("flow",))))
        return
    _check_pipeline(flow, ("flow",), provenance, problems)


def _check_pipeline(node, path, provenance, problems) -> None:
    for field in PIPELINE_FIELDS:
        value = node.get(field)
        if isinstance(value, dict) and ("uri" in value or any(marker in value for marker in MARKERS)):
            problems.append(error("reserved_name",
                                  f"a node cannot be named {field!r} (at {dotted(path)})",
                                  provenance.get(path + (field,))))
    for key, child in node.items():
        if key in PIPELINE_FIELDS:
            continue
        _check_node(child, path + (key,), provenance, problems)


def _check_node(node, path, provenance, problems) -> None:
    source = provenance.get(path)
    if not isinstance(node, dict) or not node:
        problems.append(error("ambiguous_node", f"{dotted(path)} is not a valid node", source))
        return
    if "uri" in node:
        return
    markers = [marker for marker in MARKERS if marker in node]
    if len(markers) > 1:
        problems.append(error("ambiguous_node",
                              f"{dotted(path)} mixes {' and '.join(markers)}", source))
        return
    if markers:
        marker = markers[0]
        inside = node[marker]
        if not isinstance(inside, dict):
            problems.append(error("ambiguous_node", f"{marker} must be a mapping at {dotted(path)}", source))
            return
        if marker == "branch":
            cases = inside.get("cases")
            if isinstance(cases, dict):
                for label, case in cases.items():
                    if isinstance(label, str) and label.lower() in ("true", "false"):
                        problems.append(warning("quoted_bool_label",
                                                f"case label {label!r} at {dotted(path)} is a string",
                                                provenance.get(path + ("branch", "cases", label)),
                                                hint="write it unquoted to match a boolean decide"))
                    _check_node(case, path + ("branch", "cases", label), provenance, problems)
            if "default" in inside:
                _check_node(inside["default"], path + ("branch", "default"), provenance, problems)
        elif "body" in inside:
            _check_node(inside["body"], path + (marker, "body"), provenance, problems)
        return
    if any(field in node for field in STEP_ONLY_FIELDS):
        problems.append(error("ambiguous_node",
                              f"{dotted(path)} looks like a step but has no uri", source,
                              hint="did you forget uri?"))
        return
    _check_pipeline(node, path, provenance, problems)


def _check_uris(data, provenance, registry, problems) -> None:
    known = registry.uris()
    for text, path in _uri_positions(data, (), False):
        if not text.startswith("/") or text in known:
            continue
        close = difflib.get_close_matches(text, known, n=1)
        hint = f"did you mean {close[0]}?" if close else None
        problems.append(error("unknown_uri", f"{text} is not registered",
                              provenance.get(path), hint))


def _uri_positions(value, path, in_params):
    if isinstance(value, dict):
        for key, item in value.items():
            child = path + (key,)
            uri_field = key in ("uri", "builder") and not in_params
            condition_field = (not in_params and path and path[0] == "flow"
                               and key in ("when", "until", "decide"))
            if (uri_field or condition_field) and isinstance(item, str):
                yield item, child
            elif key == "params" and isinstance(item, dict):
                yield from _uri_positions(item, child, True)
            else:
                yield from _uri_positions(item, child, in_params)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _uri_positions(item, path + (index,), in_params)


def _check_signatures(data, provenance, expansions, registry, problems) -> None:
    targets = []
    for path, component in components(data):
        if isinstance(component.get("uri"), str):
            params = component.get("params")
            params = params if isinstance(params, dict) else {}
            targets.append((component["uri"], params, bool(component.get("partial")), path))
    paths = {dotted(path): path for path, _ in components(data)}
    for key, graph in expansions.items():
        for node in graph["graph"].values():
            targets.append((node["uri"], node["params"], node["partial"], paths.get(key, ())))
    for uri, params, partial, path in targets:
        target = registry.resolve_quietly(uri)
        if target is None:
            continue
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            continue
        parameters = signature.parameters.values()
        accepts_any = any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters)
        names = {parameter.name for parameter in parameters
                 if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)}
        source = provenance.get(path)
        for given in params:
            if not accepts_any and given not in names:
                problems.append(error("signature_mismatch",
                                      f"{uri} has no parameter {given!r} (at {dotted(path)})",
                                      source))
        if not partial:
            required = {parameter.name for parameter in parameters
                        if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
                        and parameter.default is parameter.empty}
            for missing in sorted(required - set(params)):
                problems.append(error("signature_mismatch",
                                      f"{uri} requires parameter {missing!r} (at {dotted(path)})",
                                      source))
