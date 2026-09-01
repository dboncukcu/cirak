from .errors import Problem, dotted, error
from .resolve import fill

RESERVED = ("include", "plugins", "alias", "params", "blocks", "setup", "flow")


def components(data: dict) -> list[tuple[tuple, dict]]:
    found: list[tuple[tuple, dict]] = []
    for key, value in data.items():
        if key in RESERVED or not isinstance(value, dict):
            continue
        _collect(value, (key,), found)
    return found


def _collect(value, path, found) -> None:
    if "uri" in value or "block" in value:
        found.append((path, value))
        return
    for key, child in value.items():
        if isinstance(child, dict):
            _collect(child, path + (key,), found)


def expand(data: dict, provenance: dict) -> tuple[dict[str, dict], list[Problem]]:
    blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
    expansions: dict[str, dict] = {}
    problems: list[Problem] = []
    for path, component in components(data):
        name = component.get("block")
        if not isinstance(name, str):
            continue
        params = component.get("params") if isinstance(component.get("params"), dict) else {}
        graph = _expand_block(blocks, name, params, path, (), provenance, problems)
        if graph is not None:
            expansions[dotted(path)] = graph
    return expansions, problems


def _expand_block(blocks, name, args, use_path, stack, provenance, problems):
    if name in stack:
        chain = " -> ".join([*stack, name])
        problems.append(error("block_cycle", f"block cycle: {chain}",
                              provenance.get(("blocks", name))))
        return None
    definition = blocks.get(name)
    if not isinstance(definition, dict):
        problems.append(error("unknown_block", f"block {name!r} is not defined (used at {dotted(use_path)})",
                              provenance.get(use_path)))
        return None
    values, filled = _variables(definition, name, args, use_path, provenance, problems)
    if not filled:
        return None
    has_spec = isinstance(definition.get("spec"), list)
    has_graph = isinstance(definition.get("graph"), dict)
    if has_spec == has_graph:
        problems.append(error("invalid_block", f"block {name!r} must define exactly one of spec or graph",
                              provenance.get(("blocks", name))))
        return None
    inner_stack = stack + (name,)
    if has_spec:
        return _expand_spec(blocks, name, definition, values, inner_stack, provenance, problems)
    return _expand_graph(blocks, name, definition, values, inner_stack, provenance, problems)


def _variables(definition, name, args, use_path, provenance, problems):
    declared = definition.get("variables") if isinstance(definition.get("variables"), dict) else {}
    values: dict = {}
    filled = True
    for var, spec in declared.items():
        spec = spec if isinstance(spec, dict) else {}
        if "default" in spec:
            values[var] = spec["default"]
        elif not spec.get("required"):
            problems.append(error("invalid_block",
                                  f"variable {var!r} of block {name!r} must set required or default",
                                  provenance.get(("blocks", name, "variables", var))))
            filled = False
    for var, value in args.items():
        if var not in declared:
            problems.append(error("unknown_param",
                                  f"block {name!r} has no variable {var!r} (used at {dotted(use_path)})",
                                  provenance.get(use_path)))
            filled = False
        else:
            values[var] = value
    for var in declared:
        if var not in values and filled:
            problems.append(error("missing_variable",
                                  f"block {name!r} requires variable {var!r} (used at {dotted(use_path)})",
                                  provenance.get(use_path)))
            filled = False
    return values, filled


def _expand_spec(blocks, name, definition, values, stack, provenance, problems):
    nodes: dict = {}
    previous = "s_in"
    counter = 0
    for position, item in enumerate(definition["spec"]):
        item_path = ("blocks", name, "spec", position)
        if not isinstance(item, dict) or (("uri" in item) == ("block" in item)):
            problems.append(error("invalid_block",
                                  f"spec item {dotted(item_path)} must have exactly one of uri or block",
                                  provenance.get(item_path)))
            return None
        repeat = fill(item.get("repeat", 1), values)
        if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0:
            problems.append(error("bad_repeat",
                                  f"repeat must be a non negative integer at {dotted(item_path)}, got {repeat!r}",
                                  provenance.get(item_path)))
            return None
        for _ in range(repeat):
            node_name = f"s{counter}"
            counter += 1
            if "uri" in item:
                nodes[node_name] = _node(item, values, [previous], [node_name])
            else:
                inner = _expand_block(blocks, item["block"],
                                      fill(item.get("params", {}), values),
                                      item_path, stack, provenance, problems)
                if inner is None:
                    return None
                if len(inner["inputs"]) != 1 or len(inner["outputs"]) != 1:
                    problems.append(error("spec_arity",
                                          f"block {item['block']!r} used in spec at {dotted(item_path)} "
                                          f"must have one input and one output",
                                          provenance.get(item_path)))
                    return None
                _splice(nodes, node_name, inner, [previous], [node_name])
            previous = node_name
    return {"inputs": ["s_in"], "outputs": [previous], "graph": nodes}


def _expand_graph(blocks, name, definition, values, stack, provenance, problems):
    inputs = definition.get("inputs")
    outputs = definition.get("outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        problems.append(error("invalid_block",
                              f"graph block {name!r} must declare inputs and outputs lists",
                              provenance.get(("blocks", name))))
        return None
    nodes: dict = {}
    for node_name, item in definition["graph"].items():
        item_path = ("blocks", name, "graph", node_name)
        if not isinstance(item, dict) or (("uri" in item) == ("block" in item)):
            problems.append(error("invalid_block",
                                  f"graph node {dotted(item_path)} must have exactly one of uri or block",
                                  provenance.get(item_path)))
            return None
        if "repeat" in item:
            problems.append(error("invalid_block",
                                  f"repeat is only valid in spec items, found at {dotted(item_path)}",
                                  provenance.get(item_path)))
            return None
        node_inputs = item.get("inputs", [])
        node_inputs = list(node_inputs) if isinstance(node_inputs, list) else [node_inputs]
        node_outputs = item.get("outputs", [node_name])
        node_outputs = list(node_outputs) if isinstance(node_outputs, list) else [node_outputs]
        if "uri" in item:
            nodes[node_name] = _node(item, values, node_inputs, node_outputs)
            continue
        inner = _expand_block(blocks, item["block"],
                              fill(item.get("params", {}), values),
                              item_path, stack, provenance, problems)
        if inner is None:
            return None
        if len(inner["inputs"]) != len(node_inputs) or len(inner["outputs"]) != len(node_outputs):
            problems.append(error("spec_arity",
                                  f"graph node {dotted(item_path)} wires {len(node_inputs)} inputs and "
                                  f"{len(node_outputs)} outputs but block {item['block']!r} declares "
                                  f"{len(inner['inputs'])} and {len(inner['outputs'])}",
                                  provenance.get(item_path)))
            return None
        _splice(nodes, node_name, inner, node_inputs, node_outputs)
    return {"inputs": list(inputs), "outputs": list(outputs), "graph": nodes}


def _node(item, values, inputs, outputs) -> dict:
    return {"uri": item["uri"],
            "params": fill(item.get("params", {}), values),
            "partial": bool(item.get("partial", False)),
            "inputs": inputs,
            "outputs": outputs}


def _splice(nodes, prefix, inner, outer_inputs, outer_outputs) -> None:
    wires: dict[str, str] = {}
    for position, wire in enumerate(inner["inputs"]):
        wires[wire] = outer_inputs[position]
    for position, wire in enumerate(inner["outputs"]):
        wires[wire] = outer_outputs[position]

    def rename(wire):
        return wires.get(wire, f"{prefix}.{wire}")

    for inner_name, node in inner["graph"].items():
        nodes[f"{prefix}.{inner_name}"] = {**node,
                                           "inputs": [rename(wire) for wire in node["inputs"]],
                                           "outputs": [rename(wire) for wire in node["outputs"]]}
