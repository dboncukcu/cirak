from .errors import Problem, dotted, error
from .resolve import FillError, fill

RESERVED = ("include", "plugins", "alias", "params", "blocks", "setup", "flow")
PIPELINE_FIELDS = ("inputs", "outputs", "wait_for")
MARKERS = ("map", "loop", "branch")
FOREACH_FIELDS = ("over", "item", "index", "count", "chain", "key", "node")


def components(data: dict) -> list[tuple[tuple, dict]]:
    found: list[tuple[tuple, dict]] = []
    for key, value in data.items():
        if key in RESERVED or not isinstance(value, dict):
            continue
        _collect(value, (key,), found)
    return found


def empty_groups(data: dict) -> list[str]:
    """Top level sections that are empty mappings: groups with no members, referenced as ``{}``."""
    return [key for key, value in data.items()
            if key not in RESERVED and isinstance(value, dict) and not value]


def _collect(value, path, found) -> None:
    if "uri" in value or "block" in value:
        found.append((path, value))
        return
    for key, child in value.items():
        if isinstance(child, dict):
            _collect(child, path + (key,), found)


def builder_steps(flow, path=("flow",)):
    """Every ``{block, builder}`` step of an expanded flow, with its path."""
    if not isinstance(flow, dict):
        return
    for key, node in flow.items():
        if key in PIPELINE_FIELDS or not isinstance(node, dict):
            continue
        child = path + (key,)
        if "block" in node and "builder" in node:
            yield child, node
        elif "uri" in node:
            continue
        elif "map" in node or "loop" in node:
            inside = node["map"] if "map" in node else node["loop"]
            marker = "map" if "map" in node else "loop"
            if isinstance(inside, dict) and isinstance(inside.get("body"), dict):
                yield from builder_steps(inside["body"], child + (marker, "body"))
        elif "branch" in node:
            inside = node["branch"]
            if isinstance(inside, dict):
                for label, case in (inside.get("cases") or {}).items():
                    yield from builder_steps({label: case}, child + ("branch", "cases"))
                if isinstance(inside.get("default"), dict):
                    yield from builder_steps({"default": inside["default"]}, child + ("branch",))
        else:
            yield from builder_steps(node, child)


def expand(data: dict, provenance: dict, flow=None, flow_provenance=None) -> tuple[dict[str, dict], list[Problem]]:
    """Expand every graph block a component or a builder step uses, keyed by dotted path."""
    blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
    expansions: dict[str, dict] = {}
    problems: list[Problem] = []
    uses = [(path, component, provenance) for path, component in components(data)]
    for path, step in builder_steps(flow if flow is not None else data.get("flow")):
        uses.append((path, step, flow_provenance if flow_provenance is not None else provenance))
    for path, component, source_map in uses:
        name = component.get("block")
        if not isinstance(name, str):
            continue
        params = component.get("params") if isinstance(component.get("params"), dict) else {}
        args = {} if "builder" in component and path[0] == "flow" else params
        graph = _expand_block(blocks, name, args, path, (), source_map, problems)
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
    if isinstance(definition.get("flow"), dict):
        problems.append(error("invalid_block",
                              f"block {name!r} is a flow block and cannot be built into an object "
                              f"(used at {dotted(use_path)})", provenance.get(use_path)))
        return None
    if has_spec == has_graph:
        problems.append(error("invalid_block", f"block {name!r} must define exactly one of spec, graph or flow",
                              provenance.get(("blocks", name))))
        return None
    inner_stack = stack + (name,)
    try:
        if has_spec:
            return _expand_spec(blocks, name, definition, values, inner_stack, provenance, problems)
        return _expand_graph(blocks, name, definition, values, inner_stack, provenance, problems)
    except FillError as exc:
        problems.append(error("unknown_variable", f"block {name!r}: {exc}", provenance.get(("blocks", name))))
        return None


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


NODE_FIELDS = ("uri", "block", "model", "params", "partial", "unpack", "inputs", "outputs", "repeat", "init")


def _node_kind(item, item_path, provenance, problems, where):
    if not isinstance(item, dict):
        problems.append(error("invalid_block", f"{where} {dotted(item_path)} must be a mapping",
                              provenance.get(item_path)))
        return None
    kinds = [kind for kind in ("uri", "block", "model") if kind in item]
    if len(kinds) != 1:
        problems.append(error("invalid_block",
                              f"{where} {dotted(item_path)} must have exactly one of uri, block or model",
                              provenance.get(item_path)))
        return None
    unknown = [key for key in item if key not in NODE_FIELDS]
    if unknown:
        problems.append(error("unknown_key", f"{where} {dotted(item_path)} has unknown keys {unknown}",
                              provenance.get(item_path)))
        return None
    kind = kinds[0]
    if kind != "uri":
        extra = [key for key in ("params", "partial", "unpack") if key in item and kind == "model"]
        extra += [key for key in ("unpack",) if key in item and kind == "block"]
        if extra:
            problems.append(error("invalid_block",
                                  f"{extra[0]} applies to uri nodes only (at {dotted(item_path)}); a {kind} "
                                  f"node wires by name and count", provenance.get(item_path)))
            return None
    return kind


def _repeat_of(item, item_path, values, provenance, problems):
    repeat = fill(item.get("repeat", 1), values)
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0:
        problems.append(error("bad_repeat",
                              f"repeat must be a non negative integer at {dotted(item_path)}, got {repeat!r}",
                              provenance.get(item_path)))
        return None
    return repeat


def _expand_spec(blocks, name, definition, values, stack, provenance, problems):
    first, last = "s_in", None
    for field in ("inputs", "outputs"):
        if field in definition:
            declared = definition[field]
            if not (isinstance(declared, list) and len(declared) == 1 and isinstance(declared[0], str)):
                problems.append(error("invalid_block",
                                      f"{field} of spec block {name!r} must name exactly one wire",
                                      provenance.get(("blocks", name, field))))
                return None
            if field == "inputs":
                first = declared[0]
            else:
                last = declared[0]
    nodes: dict = {}
    previous = first
    counter = 0
    items = definition["spec"]
    for position, item in enumerate(items):
        item_path = ("blocks", name, "spec", position)
        kind = _node_kind(item, item_path, provenance, problems, "spec item")
        if kind is None:
            return None
        repeat = _repeat_of(item, item_path, values, provenance, problems)
        if repeat is None:
            return None
        for _ in range(repeat):
            node_name = f"s{counter}"
            counter += 1
            if not _emit(nodes, node_name, kind, item, item_path, values, [previous], [node_name],
                         blocks, stack, provenance, problems):
                return None
            previous = node_name
    declared = nodes.pop("", {})
    if last is not None and previous != first:
        tail = nodes[previous]
        nodes[previous] = {**tail, "outputs": [last]}
        for node in nodes.values():
            node["inputs"] = [last if wire == previous else wire for wire in node["inputs"]]
        declared = {(last if wire == previous else wire): owner for wire, owner in declared.items()}
        previous = last
    return {"inputs": [first], "outputs": [previous], "graph": nodes, "declared": declared}


def _expand_graph(blocks, name, definition, values, stack, provenance, problems):
    inputs = definition.get("inputs")
    outputs = definition.get("outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        problems.append(error("invalid_block",
                              f"graph block {name!r} must declare inputs and outputs lists",
                              provenance.get(("blocks", name))))
        return None
    nodes: dict = {}
    aliases: dict = {}
    for node_name, item in definition["graph"].items():
        item_path = ("blocks", name, "graph", node_name)
        kind = _node_kind(item, item_path, provenance, problems, "graph node")
        if kind is None:
            return None
        repeat = _repeat_of(item, item_path, values, provenance, problems)
        if repeat is None:
            return None
        node_inputs = item.get("inputs", [])
        node_inputs = list(node_inputs) if isinstance(node_inputs, list) else [node_inputs]
        node_outputs = item.get("outputs", [node_name])
        node_outputs = list(node_outputs) if isinstance(node_outputs, list) else [node_outputs]
        if repeat == 1:
            if not _emit(nodes, node_name, kind, item, item_path, values, node_inputs, node_outputs,
                         blocks, stack, provenance, problems, "outputs" in item):
                return None
            continue
        if len(node_inputs) != 1 or len(node_outputs) != 1:
            problems.append(error("bad_repeat",
                                  f"repeat at {dotted(item_path)} chains copies, so the node needs exactly "
                                  f"one input and one output wire", provenance.get(item_path)))
            return None
        if repeat == 0:
            aliases[node_outputs[0]] = node_inputs[0]
            continue
        for copy in range(repeat):
            copy_name = f"{node_name}_{copy}"
            ins = node_inputs if copy == 0 else [f"{node_name}_{copy - 1}"]
            outs = node_outputs if copy == repeat - 1 else [copy_name]
            if not _emit(nodes, copy_name, kind, item, item_path, values, ins, outs,
                         blocks, stack, provenance, problems, "outputs" in item):
                return None
    declared = nodes.pop("", {})
    if aliases:
        def follow(wire):
            seen = set()
            while wire in aliases and wire not in seen:
                seen.add(wire)
                wire = aliases[wire]
            return wire

        for node in nodes.values():
            node["inputs"] = [follow(wire) for wire in node["inputs"]]
        outputs = [follow(wire) for wire in outputs]
    return {"inputs": list(inputs), "outputs": list(outputs), "graph": nodes, "declared": declared}


def _emit(nodes, node_name, kind, item, item_path, values, ins, outs, blocks, stack, provenance, problems,
          named_outputs=False) -> bool:
    if kind == "uri":
        nodes[node_name] = _node(item, values, ins, outs)
        return True
    if kind == "model":
        target = fill(item["model"], values)
        if not isinstance(target, str):
            problems.append(error("invalid_block", f"model of {dotted(item_path)} must be a name",
                                  provenance.get(item_path)))
            return False
        nodes[node_name] = {"ref": target, "inputs": ins, "outputs": outs, "unpack": len(outs) > 1}
        return True
    inner = _expand_block(blocks, item["block"], fill(item.get("params", {}), values),
                          item_path, stack, provenance, problems)
    if inner is None:
        return False
    if len(inner["inputs"]) != len(ins):
        problems.append(error("spec_arity",
                              f"{dotted(item_path)} wires {len(ins)} inputs but block {item['block']!r} "
                              f"declares {len(inner['inputs'])}", provenance.get(item_path)))
        return False
    if named_outputs:
        missing = [wire for wire in outs if wire not in inner["outputs"]]
        if missing:
            problems.append(error("unknown_output",
                                  f"{dotted(item_path)} names outputs {missing} that block "
                                  f"{item['block']!r} does not declare {inner['outputs']}",
                                  provenance.get(item_path)))
            return False
        wiring = {wire: wire for wire in outs}
    else:
        if len(inner["outputs"]) != len(outs):
            problems.append(error("spec_arity",
                                  f"{dotted(item_path)} takes {len(outs)} output but block {item['block']!r} "
                                  f"declares {len(inner['outputs'])}; name them with outputs",
                                  provenance.get(item_path)))
            return False
        wiring = dict(zip(inner["outputs"], outs))
    _splice(nodes, node_name, inner, ins, wiring)
    nodes.setdefault("", {}).update({outer: node_name for outer in wiring.values()})
    return True


def _node(item, values, inputs, outputs) -> dict:
    node = {"uri": item["uri"],
            "params": fill(item.get("params", {}), values),
            "partial": bool(item.get("partial", False)),
            "unpack": item.get("unpack", False),
            "inputs": inputs,
            "outputs": outputs}
    if "init" in item:
        node["init"] = fill(item["init"], values)
    return node


def _splice(nodes, prefix, inner, outer_inputs, wiring) -> None:
    wires: dict[str, str] = {}
    for position, wire in enumerate(inner["inputs"]):
        wires[wire] = outer_inputs[position]
    wires.update(wiring)

    def rename(wire):
        return wires.get(wire, f"{prefix}.{wire}")

    for inner_name, node in inner["graph"].items():
        nodes[f"{prefix}.{inner_name}"] = {**node,
                                           "inputs": [rename(wire) for wire in node["inputs"]],
                                           "outputs": [rename(wire) for wire in node["outputs"]]}
    for wire, owner in inner.get("declared", {}).items():
        nodes.setdefault("", {})[rename(wire)] = f"{prefix}.{owner}"


def expand_flow(data: dict, provenance: dict) -> tuple[dict | None, dict, list[Problem]]:
    """Open every flow block usage and foreach of the root flow.

    Returns the expanded flow, the provenance of its nodes (pointing at the
    templates they came from) and the problems. Builder steps stay as they are.
    """
    flow = data.get("flow")
    if not isinstance(flow, dict):
        return flow, {}, []
    blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
    problems: list[Problem] = []
    expander = _FlowExpander(blocks, provenance, problems)
    expanded: dict = {}
    origins: dict = {}
    try:
        expander.pipeline(flow, ("flow",), ("flow",), (), expanded, origins)
    except _Failed:
        pass
    return expanded, origins, problems


class _Failed(Exception):
    pass


class _FlowExpander:
    def __init__(self, blocks, provenance, problems):
        self.blocks = blocks
        self.provenance = provenance
        self.problems = problems

    def fail(self, kind, message, source_path):
        self.problems.append(error(kind, message, self.provenance.get(source_path)))
        raise _Failed()

    def pipeline(self, spec, out_path, src_path, stack, out, origins) -> None:
        for key, value in spec.items():
            child_out = out_path + (key,)
            child_src = src_path + (key,)
            if key in PIPELINE_FIELDS:
                out[key] = value
                self.stamp(value, child_out, child_src, origins)
                continue
            if isinstance(value, dict) and "foreach" in value:
                for name, node, node_src in self.foreach(key, value, child_src):
                    self.node(node, out_path + (name,), node_src, stack, out, origins, name)
                continue
            self.node(value, child_out, child_src, stack, out, origins, key)

    def node(self, value, out_path, src_path, stack, out, origins, name) -> None:
        if not isinstance(value, dict):
            out[name] = value
            self.stamp(value, out_path, src_path, origins)
            return
        if "block" in value and "builder" not in value and "uri" not in value:
            try:
                expanded = self.block_usage(value, out_path, src_path, stack, origins)
            except _Failed:
                return
            if expanded is not None:
                out[name] = expanded
            return
        if "uri" in value or ("block" in value and "builder" in value):
            out[name] = _tidy(value)
            self.stamp(out[name], out_path, src_path, origins)
            return
        marker = next((marker for marker in MARKERS if marker in value), None)
        if marker is not None:
            inside = value[marker]
            if not isinstance(inside, dict):
                out[name] = value
                self.stamp(value, out_path, src_path, origins)
                return
            copy = {}
            for field, item in inside.items():
                field_out = out_path + (marker, field)
                field_src = src_path + (marker, field)
                if field == "body" and isinstance(item, dict):
                    holder: dict = {}
                    self.node(item, field_out, field_src, stack, holder, origins, "body")
                    copy[field] = holder.get("body", item)
                elif field == "cases" and isinstance(item, dict):
                    cases: dict = {}
                    for label, case in item.items():
                        self.node(case, field_out + (label,), field_src + (label,), stack, cases, origins, label)
                    copy[field] = cases
                elif field == "default" and isinstance(item, dict):
                    holder = {}
                    self.node(item, field_out, field_src, stack, holder, origins, "default")
                    copy[field] = holder.get("default", item)
                else:
                    copy[field] = item
                    self.stamp(item, field_out, field_src, origins)
            out[name] = {marker: copy}
            origins[out_path] = self.provenance.get(src_path)
            origins[out_path + (marker,)] = self.provenance.get(src_path + (marker,))
            return
        nested: dict = {}
        origins[out_path] = self.provenance.get(src_path)
        self.pipeline(value, out_path, src_path, stack, nested, origins)
        out[name] = nested

    def stamp(self, value, out_path, src_path, origins) -> None:
        origins[out_path] = self.provenance.get(src_path)
        if isinstance(value, dict):
            for key, item in value.items():
                self.stamp(item, out_path + (key,), src_path + (key,), origins)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self.stamp(item, out_path + (index,), src_path + (index,), origins)

    def block_usage(self, usage, out_path, src_path, stack, origins):
        name = usage["block"]
        if not isinstance(name, str):
            self.fail("invalid_block", f"block of {dotted(out_path)} must be a string", src_path)
            return None
        unknown = [key for key in usage if key not in ("block", "params", "inputs", "outputs", "wait_for")]
        if unknown:
            self.fail("invalid_block", f"flow block usage {dotted(out_path)} has unknown keys {unknown}", src_path)
            return None
        if name in stack:
            self.fail("block_cycle", "block cycle: " + " -> ".join([*stack, name]), src_path)
            return None
        definition = self.blocks.get(name)
        if not isinstance(definition, dict):
            self.fail("unknown_block", f"block {name!r} is not defined (used at {dotted(out_path)})", src_path)
            return None
        if not isinstance(definition.get("flow"), dict):
            self.fail("invalid_block",
                      f"block {name!r} has no flow; a flow usage needs a flow block (used at {dotted(out_path)})",
                      src_path)
            return None
        params = usage.get("params") if isinstance(usage.get("params"), dict) else {}
        if "params" in usage and not isinstance(usage["params"], dict):
            self.fail("invalid_block", f"params of {dotted(out_path)} must be a mapping", src_path)
            return None
        values, filled = _variables(definition, name, params, src_path, self.provenance, self.problems)
        if not filled:
            return None
        template_path = ("blocks", name, "flow")
        try:
            counts = self.counts(definition["flow"], template_path, values, name)
            if counts is None:
                return None
            values.update(counts)
            filled_flow = fill(definition["flow"], values)
        except FillError as exc:
            self.fail("unknown_variable", f"block {name!r}: {exc}", template_path)
            return None
        expanded: dict = {}
        origins[out_path] = self.provenance.get(src_path)
        self.pipeline(filled_flow, out_path, template_path, stack + (name,), expanded, origins)
        for field in ("inputs", "outputs", "wait_for"):
            if field in usage:
                expanded[field] = usage[field]
                self.stamp(usage[field], out_path + (field,), src_path + (field,), origins)
        return expanded

    def counts(self, flow, template_path, values, block_name):
        found: dict = {}
        for path, spec in self.foreach_specs(flow, template_path):
            count = spec.get("count")
            if count is None:
                continue
            if not isinstance(count, str):
                self.fail("invalid_foreach", f"count of foreach {dotted(path)} must be a name", path)
                return None
            if count in values or count in found:
                self.fail("invalid_foreach",
                          f"count name {count!r} of foreach {dotted(path)} is already a variable", path)
                return None
            over = fill(spec.get("over"), values)
            if not isinstance(over, (list, dict)):
                self.fail("invalid_foreach",
                          f"over of foreach {dotted(path)} must be a list or a mapping, got {over!r}", path)
                return None
            found[count] = len(over)
        return found

    def foreach_specs(self, value, path):
        if isinstance(value, dict):
            if isinstance(value.get("foreach"), dict):
                yield path, value["foreach"]
                return
            for key, item in value.items():
                yield from self.foreach_specs(item, path + (key,))

    def foreach(self, name, holder, src_path):
        spec = holder["foreach"]
        path = src_path + ("foreach",)
        if not isinstance(spec, dict):
            self.fail("invalid_foreach", f"foreach at {dotted(src_path)} must be a mapping", src_path)
            return []
        unknown = [key for key in spec if key not in FOREACH_FIELDS]
        if unknown or len(holder) != 1:
            extra = unknown or [key for key in holder if key != "foreach"]
            self.fail("invalid_foreach", f"foreach at {dotted(src_path)} has unknown keys {extra}", src_path)
            return []
        over = spec.get("over")
        if not isinstance(over, (list, dict)):
            self.fail("invalid_foreach",
                      f"over of foreach {dotted(src_path)} must be a list or a mapping, got {over!r}", src_path)
            return []
        node = spec.get("node")
        if not isinstance(node, dict):
            self.fail("invalid_foreach", f"foreach at {dotted(src_path)} needs a node template", src_path)
            return []
        if "foreach" in node or any(isinstance(item, dict) and "foreach" in item for item in node.values()):
            self.fail("invalid_foreach", f"foreach at {dotted(src_path)} nests another foreach", src_path)
            return []
        item_name = spec.get("item", "item")
        index_name = spec.get("index")
        chain = spec.get("chain")
        for field, value in (("item", item_name), ("index", index_name), ("chain", chain)):
            if value is not None and not isinstance(value, str):
                self.fail("invalid_foreach", f"{field} of foreach {dotted(src_path)} must be a name", src_path)
        if chain is not None:
            if "uri" not in node:
                self.fail("invalid_foreach",
                          f"chain of foreach {dotted(src_path)} needs a step template with a uri", src_path)
            if "inputs" in node or "outputs" in node:
                self.fail("invalid_foreach",
                          f"foreach {dotted(src_path)} chains {chain!r}, so its node cannot write inputs or "
                          f"outputs", src_path)
        entries = list(over.items()) if isinstance(over, dict) else [(None, item) for item in over]
        produced = []
        for position, (mapping_key, item) in enumerate(entries, 1):
            local = {item_name: item}
            if index_name is not None:
                local[index_name] = position
            try:
                copy = fill(node, local)
                suffix = fill(spec["key"], local) if "key" in spec else (
                    mapping_key if mapping_key is not None else position)
            except FillError as exc:
                self.fail("unknown_variable", f"foreach {dotted(src_path)}: {exc}", src_path)
            if isinstance(suffix, bool) or not isinstance(suffix, (str, int)):
                self.fail("invalid_foreach",
                          f"key of foreach {dotted(src_path)} must name each node with a string or an int, "
                          f"got {suffix!r}", src_path)
            if chain is not None:
                copy = {**copy, "inputs": {chain: f"{chain}_{position - 1}"}, "outputs": [f"{chain}_{position}"]}
            produced.append((f"{name}_{suffix}", copy, path + ("node",)))
        return produced


def _tidy(node: dict) -> dict:
    if node.get("params") == {}:
        return {key: value for key, value in node.items() if key != "params"}
    return node
