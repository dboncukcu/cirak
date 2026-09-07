import difflib
import inspect

from .errors import Problem, dotted, error, warning
from .expand import builder_steps, components, empty_groups
from .resolve import TOKEN

PIPELINE_FIELDS = ("inputs", "outputs", "wait_for")
MARKERS = ("map", "loop", "branch")
STEP_ONLY_FIELDS = ("params", "when", "retries", "wait", "unpack")


def validate(data: dict, provenance: dict, expansions: dict, registry=None) -> list[Problem]:
    problems: list[Problem] = []
    _check_setup(data, provenance, problems)
    _check_components(data, provenance, problems)
    _check_blocks(data, provenance, problems)
    _check_graphs(data, provenance, expansions, problems)
    _check_references(data, provenance, problems)
    _check_inline_components(data, provenance, problems)
    _check_flow(data, provenance, registry, problems)
    if registry is not None:
        _check_uris(data, provenance, registry, problems)
        _check_signatures(data, provenance, expansions, registry, problems)
        _check_kinds(data, provenance, registry, problems)
    return problems


def _check_setup(data, provenance, problems) -> None:
    setup = data.get("setup")
    if setup is None:
        return
    if not isinstance(setup, list):
        problems.append(error("invalid_setup", "setup must be a list of steps",
                              provenance.get(("setup",))))
        return
    for index, step in enumerate(setup):
        source = provenance.get(("setup", index))
        if not isinstance(step, dict) or not isinstance(step.get("uri"), str):
            problems.append(error("invalid_setup",
                                  f"setup[{index}] must be a mapping with a uri", source))
            continue
        params = step.get("params")
        if params is not None and not isinstance(params, dict):
            problems.append(error("invalid_setup",
                                  f"params of setup[{index}] must be a mapping", source))
        unknown = [key for key in step if key not in ("uri", "params")]
        if unknown:
            problems.append(error("invalid_setup",
                                  f"setup[{index}] has unknown keys {unknown}", source))
        for ref, ref_path in _refs(params if isinstance(params, dict) else {},
                                   ("setup", index, "params")):
            problems.append(error("invalid_setup",
                                  f"@{ref} is not available in setup: components are built "
                                  f"after setup runs (at {dotted(ref_path)})",
                                  provenance.get(ref_path)))
        for _, inline_path in _inline_mappings(params if isinstance(params, dict) else {},
                                               ("setup", index, "params")):
            problems.append(error("invalid_setup",
                                  f"inline components are not available in setup (at {dotted(inline_path)})",
                                  provenance.get(inline_path)))


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
        bodies = [kind for kind in ("spec", "graph", "flow") if kind in definition]
        if len(bodies) != 1:
            problems.append(error("invalid_block",
                                  f"block {name!r} must define exactly one of spec, graph or flow", source))
        used: set[str] = set()
        _used_names(definition.get("spec"), used)
        _used_names(definition.get("graph"), used)
        _used_names(definition.get("flow"), used)
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
                used.add(match.group(1).split(".")[0])


def _check_graphs(data, provenance, expansions, problems) -> None:
    paths = {dotted(path): path for path, _ in components(data)}
    paths.update({dotted(path): path for path, _ in builder_steps(data.get("flow"))})
    for key, graph in expansions.items():
        source = provenance.get(paths.get(key, ()))
        inputs = set(graph["inputs"])
        producer: dict[str, str] = {}
        for node_name, node in graph["graph"].items():
            _check_unpack(node.get("unpack", False), node["outputs"], f"node {node_name!r} of {key}",
                          source, problems, "sequence is spread by position")
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
        for wire, owner in graph.get("declared", {}).items():
            outside = any(wire in node["inputs"] for node_name, node in graph["graph"].items()
                          if not node_name.startswith(owner + "."))
            if wire not in graph["outputs"] and not outside and wire in consumed:
                problems.append(warning("unused_output",
                                        f"output {wire!r} of block node {owner!r} in {key} is only used "
                                        f"inside that block, nothing outside consumes it", source))
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
    groups: set[str] = set(empty_groups(data))
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


def _check_flow(data, provenance, registry, problems) -> None:
    flow = data.get("flow")
    if flow is None:
        return
    if not isinstance(flow, dict):
        problems.append(error("ambiguous_node", "flow must be a mapping of nodes",
                              provenance.get(("flow",))))
        return
    _check_pipeline(flow, ("flow",), provenance, registry, problems)


def _check_pipeline(node, path, provenance, registry, problems) -> None:
    for field in PIPELINE_FIELDS:
        value = node.get(field)
        if isinstance(value, dict) and ("uri" in value or any(marker in value for marker in MARKERS)):
            problems.append(error("reserved_name",
                                  f"a node cannot be named {field!r} (at {dotted(path)})",
                                  provenance.get(path + (field,))))
    for key, child in node.items():
        if key in PIPELINE_FIELDS:
            continue
        _check_node(child, path + (key,), provenance, registry, problems)


def _check_node(node, path, provenance, registry, problems) -> None:
    source = provenance.get(path)
    if not isinstance(node, dict) or not node:
        problems.append(error("ambiguous_node", f"{dotted(path)} is not a valid node", source))
        return
    if "uri" in node:
        _check_step(node, path, provenance, registry, problems)
        return
    if "block" in node and "builder" in node:
        _check_builder_step(node, path, provenance, registry, problems)
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
            _check_predicate(inside.get("decide"), path + ("branch", "decide"), provenance, registry,
                             problems, "decide", key_allowed=False)
            cases = inside.get("cases")
            if isinstance(cases, dict):
                for label, case in cases.items():
                    if isinstance(label, str) and label.lower() in ("true", "false"):
                        problems.append(warning("quoted_bool_label",
                                                f"case label {label!r} at {dotted(path)} is a string",
                                                provenance.get(path + ("branch", "cases", label)),
                                                hint="write it unquoted to match a boolean decide"))
                    _check_node(case, path + ("branch", "cases", label), provenance, registry, problems)
            if "default" in inside:
                _check_node(inside["default"], path + ("branch", "default"), provenance, registry, problems)
        elif "body" in inside:
            if marker == "loop":
                _check_loop(inside, path, source, problems)
                _check_predicate(inside.get("until"), path + ("loop", "until"), provenance, registry,
                                 problems, "until")
            else:
                _check_map(inside, path, source, problems)
            _check_node(inside["body"], path + (marker, "body"), provenance, registry, problems)
        else:
            problems.append(error("ambiguous_node", f"{marker} at {dotted(path)} needs a body", source))
        return
    if any(field in node for field in STEP_ONLY_FIELDS):
        problems.append(error("ambiguous_node",
                              f"{dotted(path)} looks like a step but has no uri", source,
                              hint="did you forget uri?"))
        return
    _check_pipeline(node, path, provenance, registry, problems)


def _check_loop(inside, path, source, problems) -> None:
    where = dotted(path)
    carry = inside.get("carry")
    if not (isinstance(carry, (list, dict)) and carry
            and all(isinstance(key, str) for key in carry)
            and (not isinstance(carry, dict) or all(isinstance(value, str) for value in carry.values()))):
        problems.append(error("invalid_loop", f"carry of loop {where} must be a list of keys or a mapping "
                                              f"of carry name to parent key", source))
    if "range" not in inside:
        problems.append(error("invalid_loop", f"loop {where} needs a range: an int, [start, stop], "
                                              f"[start, stop, step] or a bus key", source))
    else:
        span = inside["range"]
        ok = (isinstance(span, int) and not isinstance(span, bool)) or isinstance(span, str) or (
            isinstance(span, list) and 1 <= len(span) <= 3
            and all(isinstance(part, int) and not isinstance(part, bool) for part in span))
        if not ok:
            problems.append(error("invalid_loop", f"range of loop {where} must be an int, [start, stop], "
                                                  f"[start, stop, step] or a bus key", source))
    following = inside.get("next")
    if following is not None and not (
            (isinstance(following, str) and following)
            or (isinstance(following, dict) and all(isinstance(key, str) and isinstance(value, str)
                                                    for key, value in following.items()))):
        problems.append(error("invalid_loop", f"next of loop {where} must be a suffix or a mapping of "
                                              f"carry name to body key", source))
    for field in ("trace", "outputs"):
        value = inside.get(field)
        if value is not None and not (isinstance(value, dict) and all(
                isinstance(key, str) and isinstance(item, str) for key, item in value.items())):
            problems.append(error("invalid_loop", f"{field} of loop {where} must map inner keys to parent "
                                                  f"keys", source))
    if "index" in inside and not isinstance(inside["index"], str):
        problems.append(error("invalid_loop", f"index of loop {where} must be a key name", source))


def _check_map(inside, path, source, problems) -> None:
    where = dotted(path)
    if not isinstance(inside.get("over"), str):
        problems.append(error("invalid_map", f"over of map {where} must name the bus key of the collection",
                              source))
    for field in ("item", "index"):
        if field in inside and not isinstance(inside[field], str):
            problems.append(error("invalid_map", f"{field} of map {where} must be a key name", source))
    collect = inside.get("collect")
    if collect is not None and not (
            (isinstance(collect, list) and all(isinstance(key, str) for key in collect))
            or (isinstance(collect, dict) and all(isinstance(key, str) and isinstance(item, str)
                                                  for key, item in collect.items()))):
        problems.append(error("invalid_map", f"collect of map {where} must be a list of body keys or a "
                                             f"mapping of body key to parent key", source))
    parallel = inside.get("parallel")
    if parallel is not None and not isinstance(parallel, (bool, int)):
        problems.append(error("invalid_map", f"parallel of map {where} must be a boolean or an int", source))


def _check_step(node, path, provenance, registry, problems) -> None:
    source = provenance.get(path)
    where = dotted(path)
    outputs = node.get("outputs")
    if isinstance(outputs, str):
        outputs = [outputs]
    unpack = node.get("unpack", False)
    if isinstance(outputs, dict):
        if unpack is False and "unpack" in node:
            problems.append(error("invalid_unpack",
                                  f"{where} maps its outputs, which always unpacks; drop unpack: false",
                                  source))
        elif not outputs:
            problems.append(error("invalid_unpack", f"{where} needs at least one entry in its outputs mapping",
                                  source))
    elif isinstance(outputs, list):
        _check_unpack(unpack, outputs, where, source, problems, "mapping is picked apart by name")
    elif outputs is not None:
        problems.append(error("invalid_outputs", f"outputs of {where} must be a list or a mapping", source))
    elif not isinstance(unpack, bool):
        problems.append(error("invalid_unpack", f"unpack of {where} must be a boolean", source))
    if "passthrough" in node and not isinstance(node["passthrough"], bool):
        problems.append(error("invalid_passthrough", f"passthrough of {where} must be a boolean", source))
    params = node.get("params")
    if params is not None and not isinstance(params, dict):
        problems.append(error("invalid_params", f"params of {where} must be a mapping", source))
        params = None
    inputs = node.get("inputs")
    bound = _binding_names(inputs, where, source, problems)
    _check_predicate(node.get("when"), path + ("when",), provenance, registry, problems, "when")
    if registry is None or not isinstance(node.get("uri"), str):
        return
    for uri, inline_params, partial, inline_path in _inline_targets(params or {}, path + ("params",), registry):
        _check_target_signature(uri, inline_params, partial, inline_path,
                                provenance.get(inline_path) or source, registry, problems)
    target = registry.resolve_quietly(node["uri"])
    if target is None:
        return
    _check_step_signature(node["uri"], target, params or {}, inputs, bound, where, source, problems)


BUILDER_STEP_FIELDS = ("block", "builder", "params", "inputs", "outputs", "unpack", "when", "wait_for",
                       "retries", "wait", "passthrough")


def _check_builder_step(node, path, provenance, registry, problems) -> None:
    source = provenance.get(path)
    where = dotted(path)
    unknown = [key for key in node if key not in BUILDER_STEP_FIELDS]
    if unknown:
        problems.append(error("unknown_key", f"builder step {where} has unknown keys {unknown}", source))
    if not isinstance(node["block"], str) or not isinstance(node["builder"], str):
        problems.append(error("invalid_component", f"block and builder of {where} must be strings", source))
        return
    outputs = node.get("outputs")
    if isinstance(outputs, str):
        outputs = [outputs]
    if isinstance(outputs, list):
        _check_unpack(node.get("unpack", False), outputs, where, source, problems, "mapping is picked apart by name")
    elif outputs is not None and not isinstance(outputs, dict):
        problems.append(error("invalid_outputs", f"outputs of {where} must be a list or a mapping", source))
    params = node.get("params")
    if params is not None and not isinstance(params, dict):
        problems.append(error("invalid_params", f"params of {where} must be a mapping", source))
        params = None
    bound = _binding_names(node.get("inputs"), where, source, problems)
    _check_predicate(node.get("when"), path + ("when",), provenance, registry, problems, "when")
    if registry is None:
        return
    for uri, inline_params, partial, inline_path in _inline_targets(params or {}, path + ("params",), registry):
        _check_target_signature(uri, inline_params, partial, inline_path,
                                provenance.get(inline_path) or source, registry, problems)
    builder = registry.resolve_quietly(node["builder"])
    if builder is None:
        return
    try:
        signature = inspect.signature(builder)
    except (TypeError, ValueError):
        return
    parameters = list(signature.parameters.values())
    if parameters and parameters[0].kind is parameters[0].VAR_POSITIONAL:
        return
    if not parameters or parameters[0].kind not in (parameters[0].POSITIONAL_ONLY,
                                                    parameters[0].POSITIONAL_OR_KEYWORD):
        problems.append(error("signature_mismatch",
                              f"builder {node['builder']} must take the graph as its first parameter (at {where})",
                              source))
        return
    rest = signature.replace(parameters=parameters[1:])

    def target(*args, **kwargs):
        return None

    target.__signature__ = rest
    _check_step_signature(node["builder"], target, params or {}, node.get("inputs"), bound, where, source, problems)


def _binding_names(inputs, where, source, problems):
    if inputs is None:
        return None
    if isinstance(inputs, str):
        return [inputs]
    if isinstance(inputs, list):
        if not all(isinstance(item, str) for item in inputs):
            problems.append(error("invalid_inputs", f"inputs of {where} must list bus keys", source))
            return None
        return list(inputs)
    if isinstance(inputs, dict):
        for param, value in inputs.items():
            ok = isinstance(value, str) or (
                isinstance(value, list) and all(isinstance(item, str) for item in value)) or (
                isinstance(value, dict) and all(isinstance(key, str) and isinstance(item, str)
                                                for key, item in value.items()))
            if not isinstance(param, str) or not ok:
                problems.append(error("invalid_inputs",
                                      f"inputs of {where} must map parameter names to a bus key, "
                                      f"a pattern, a list of keys or a mapping of names to keys", source))
                return None
        return list(inputs)
    problems.append(error("invalid_inputs", f"inputs of {where} must be a list or a mapping", source))
    return None


def _check_step_signature(uri, target, params, inputs, bound, where, source, problems) -> None:
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return
    parameters = signature.parameters.values()
    accepts_any = any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters)
    names = {parameter.name for parameter in parameters
             if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)}
    for given in params:
        if not accepts_any and given not in names:
            problems.append(error("signature_mismatch",
                                  f"{uri} has no parameter {given!r} (at {where})", source))
    for name in bound or []:
        if not accepts_any and name not in names:
            problems.append(error("signature_mismatch",
                                  f"{uri} has no parameter {name!r} to bind an input to (at {where})",
                                  source))
        if name in params:
            problems.append(error("double_binding",
                                  f"parameter {name!r} of {uri} is given by both params and inputs "
                                  f"(at {where})", source))
    if bound is None:
        return
    required = {parameter.name for parameter in parameters
                if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
                and parameter.default is parameter.empty}
    for missing in sorted(required - set(params) - set(bound)):
        problems.append(error("signature_mismatch",
                              f"{uri} requires parameter {missing!r} (at {where})", source,
                              hint="bind it with inputs or give it in params"))


def _check_predicate(value, path, provenance, registry, problems, field, key_allowed=True) -> None:
    if value is None:
        return
    source = provenance.get(path)
    where = dotted(path)
    if isinstance(value, str):
        if key_allowed and value.startswith("/"):
            problems.append(error("invalid_condition",
                                  f"{field} at {where} names a bus key, and a key cannot start with '/'",
                                  source, hint=f"write {field}: {{uri: {value}}} to call a predicate"))
        return
    if not isinstance(value, dict) or not isinstance(value.get("uri"), str):
        problems.append(error("invalid_condition",
                              f"{field} at {where} must be a bus key or a {{uri, params}} predicate"
                              if key_allowed else f"{field} at {where} must be a uri or a {{uri, params}} predicate",
                              source))
        return
    if "params" in value and not isinstance(value["params"], dict):
        problems.append(error("invalid_condition", f"params of {field} at {where} must be a mapping", source))


def _check_unpack(unpack, outputs, where, source, problems, how) -> None:
    if not isinstance(unpack, bool):
        problems.append(error("invalid_unpack", f"unpack of {where} must be a boolean", source))
        return
    if unpack and not outputs:
        problems.append(error("invalid_unpack", f"{where} sets unpack: true but declares no outputs to pick",
                              source))
    if not unpack and len(outputs) > 1:
        problems.append(error("needs_unpack",
                              f"{where} declares {len(outputs)} outputs {list(outputs)}; add unpack: true "
                              f"so the returned {how}", source))


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
            uri_field = key == "uri" or (key == "builder" and not in_params)
            condition_field = (not in_params and path and path[0] == "flow" and key == "decide")
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
            partial = component.get("partial", registry.facts(component["uri"]).partial)
            targets.append((component["uri"], params, bool(partial), path))
    paths = {dotted(path): path for path, _ in components(data)}
    paths.update({dotted(path): path for path, _ in builder_steps(data.get("flow"))})
    for key, graph in expansions.items():
        for node in graph["graph"].values():
            if "uri" in node:
                targets.append((node["uri"], node["params"], node["partial"], paths.get(key, ())))
    setup = data.get("setup")
    if isinstance(setup, list):
        for index, step in enumerate(setup):
            if isinstance(step, dict) and isinstance(step.get("uri"), str):
                params = step.get("params")
                params = params if isinstance(params, dict) else {}
                targets.append((step["uri"], params, False, ("setup", index)))
    for uri, params, partial, path in list(targets):
        targets.extend(_inline_targets(params, path + ("params",), registry))
    for uri, params, partial, path in targets:
        _check_target_signature(uri, params, partial, path, provenance.get(path), registry, problems)


def _inline_targets(params, path, registry):
    """Inline components inside a params tree as (uri, params, partial, path) check targets."""
    found = []
    for inline_uri, inline_params, explicit, inline_path in inline_components(params, path):
        facts = registry.facts(inline_uri)
        partial = explicit if explicit is not None else (facts.partial or facts.kind == "data")
        found.append((inline_uri, inline_params, bool(partial), inline_path))
    return found


def _check_target_signature(uri, params, partial, path, source, registry, problems) -> None:
    target = registry.resolve_quietly(uri)
    if target is None:
        return
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return
    parameters = signature.parameters.values()
    accepts_any = any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters)
    names = {parameter.name for parameter in parameters
             if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)}
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


def inline_components(value, path):
    """Every ``{uri, params}`` mapping inside a params tree, with its explicit partial flag."""
    if isinstance(value, dict):
        if isinstance(value.get("uri"), str):
            params = value.get("params")
            params = params if isinstance(params, dict) else {}
            yield value["uri"], params, value.get("partial"), path
            yield from inline_components(params, path + ("params",))
            return
        for key, item in value.items():
            yield from inline_components(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from inline_components(item, path + (index,))


def _check_inline_components(data, provenance, problems) -> None:
    for owner_path, params in _param_trees(data):
        for value, path in _inline_mappings(params, owner_path):
            source = provenance.get(path) or provenance.get(owner_path[:-1])
            where = dotted(path)
            unknown = [key for key in value if key not in ("uri", "params", "partial")]
            if unknown:
                problems.append(error("invalid_component",
                                      f"inline component at {where} has unknown keys {unknown}", source))
            if "params" in value and not isinstance(value["params"], dict):
                problems.append(error("invalid_component",
                                      f"params of inline component at {where} must be a mapping", source))
            if "partial" in value and not isinstance(value["partial"], bool):
                problems.append(error("invalid_component",
                                      f"partial of inline component at {where} must be a boolean", source))


def _inline_mappings(value, path):
    if isinstance(value, dict):
        if isinstance(value.get("uri"), str):
            yield value, path
            yield from _inline_mappings(value.get("params"), path + ("params",))
            return
        for key, item in value.items():
            yield from _inline_mappings(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _inline_mappings(item, path + (index,))


def _param_trees(data):
    for path, component in components(data):
        if isinstance(component.get("params"), dict):
            yield path + ("params",), component["params"]
    yield from _flow_param_trees(data.get("flow"), ("flow",))


def _flow_param_trees(value, path):
    if isinstance(value, dict):
        for key, item in value.items():
            child = path + (key,)
            if key == "params" and isinstance(item, dict):
                yield child, item
            else:
                yield from _flow_param_trees(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flow_param_trees(item, path + (index,))


def _check_kinds(data, provenance, registry, problems) -> None:
    for text, path, expected in _kind_positions(data, ()):
        kind = registry.facts(text).kind
        if kind is not None and kind != expected:
            problems.append(error("kind_mismatch",
                                  f"{text} is a {kind} lego, {dotted(path)} needs a {expected} "
                                  f"(at {dotted(path[:-1])})",
                                  provenance.get(path[:-1]) or provenance.get(path)))


def _kind_positions(value, path):
    if isinstance(value, dict):
        for key, item in value.items():
            child = path + (key,)
            if key == "builder" and isinstance(item, str):
                yield item, child, "builder"
            elif key in ("when", "until", "decide") and path and path[0] == "flow":
                if isinstance(item, str) and key == "decide":
                    yield item, child, "predicate"
                elif isinstance(item, dict) and isinstance(item.get("uri"), str):
                    yield item["uri"], child + ("uri",), "predicate"
            elif key != "params":
                yield from _kind_positions(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _kind_positions(item, path + (index,))
