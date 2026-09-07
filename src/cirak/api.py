import re
import sys
import warnings
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from tezgah import ValidationError as TezgahValidationError
from tezgah import run as tezgah_run
from tezgah import subscribe

from .build import build_components
from .errors import (
    CirakError,
    CirakWarning,
    ConfigError,
    Problem,
    Source,
    error,
    render_problems,
)
from .expand import expand, expand_flow
from .flow import compile_flow, step_outputs
from .loader import Layer, load, set_layer
from .merge import describe_layers, merge_layers
from .registry import registry as global_registry
from .resolve import resolve as resolve_values
from .validate import validate


@dataclass(frozen=True)
class Analysis:
    """Everything the compile gate knows about a recipe.

    ``data`` is the resolved recipe with blocks kept as templates; ``flow`` is
    the expanded root flow (blocks opened, foreach unrolled) and ``view`` the
    recipe with that flow in place, which validation and compilation read.
    """

    data: dict
    provenance: dict
    expansions: dict
    problems: list[Problem]
    layer: Layer = field(default_factory=Layer)
    overrides: list = field(default_factory=list)
    flow: dict | None = None
    flow_provenance: dict = field(default_factory=dict)

    @property
    def view(self) -> dict:
        if self.flow is None:
            return self.data
        return {**self.data, "flow": self.flow}

    @property
    def view_provenance(self) -> dict:
        merged = {path: source for path, source in self.provenance.items() if not path or path[0] != "flow"}
        merged.update(self.flow_provenance)
        return merged


def _layered(paths, sets):
    layer, load_problems = load(paths, global_registry.fragments())
    if sets:
        layer = Layer(files=[], below=[layer, set_layer(sets)], label="")
    data, provenance, overrides, merge_problems = merge_layers(layer)
    return layer, data, provenance, overrides, [*load_problems, *merge_problems]


def analyze(paths, sets=None) -> Analysis:
    layer, data, provenance, overrides, problems = _layered(paths, sets)
    _extend_sys_path(layer.walk())
    imported_new, plugin_problems = _import_plugins(data.get("plugins", []))
    if imported_new:
        layer, data, provenance, overrides, problems = _layered(paths, sets)
    data, resolve_problems = resolve_values(data, provenance)
    flow, flow_provenance, flow_problems = expand_flow(data, provenance)
    analysis = Analysis(data, provenance, {}, [], layer, overrides, flow, flow_provenance)
    expansions, expand_problems = expand(data, provenance, flow, analysis.view_provenance)
    collected = [*problems, *plugin_problems, *resolve_problems, *flow_problems, *expand_problems]
    collected += validate(analysis.view, analysis.view_provenance, expansions, global_registry)
    return Analysis(data, provenance, expansions, collected, layer, overrides, flow, flow_provenance)


def check(paths, *, sets=None, inputs=None) -> list[Problem]:
    """Every problem of the recipe, including what tezgah finds in the compiled flow.

    ``inputs`` names the keys run() will be given, so the static check sees
    exactly the frame the run will see.
    """
    analysis = analyze(paths, sets)
    problems = list(analysis.problems)
    if any(problem.severity == "error" for problem in problems):
        return problems
    if isinstance(analysis.data.get("flow"), dict):
        _, flow_problems = compile_checked(analysis, _input_names(inputs), dry=True)
        problems.extend(flow_problems)
    return problems


def _input_names(inputs):
    if inputs is None:
        return []
    return list(inputs)


def compile_checked(analysis, names, dry):
    """Compile the flow and validate it on tezgah; returns the pipeline and the problems."""
    store = build_components(analysis.data, analysis.expansions, global_registry, dry=dry)
    try:
        pipeline = compile_flow(analysis.view, store, global_registry)
    except (CirakError, TypeError, ValueError) as exc:
        return None, [error("compile_failed", str(exc))]
    try:
        plan = pipeline.validate(names)
    except TezgahValidationError as exc:
        return pipeline, _tezgah_problems(exc, analysis.view_provenance)
    problems = []
    for key in plan.inputs:
        if key not in names:
            problems.append(error("missing_input",
                                  f"the flow reads key {key!r} but nothing produces it",
                                  _reader_source(plan, key, analysis.view_provenance),
                                  hint="produce it in the flow or pass it with inputs"))
    for key in names:
        if key not in plan.inputs:
            problems.append(error("unexpected_input",
                                  f"input {key!r} is given but the flow never reads it"))
    return pipeline, problems


def _reader_source(plan, key, provenance):
    for name in _readers(plan, key):
        source = _flow_source(f"'{name}'", provenance)
        if source is not None:
            return source
    return None


def _readers(resolution, key):
    table = getattr(resolution, "table", None)
    if table:
        for name, entry in table.items():
            if getattr(entry, "table", None) or getattr(entry, "body", None) or getattr(entry, "cases", None):
                yield from _readers(entry, key)
            elif key in entry.reads:
                yield name
    body = getattr(resolution, "body", None)
    if body is not None:
        yield from _readers(body, key)
    for case in (getattr(resolution, "cases", None) or {}).values():
        yield from _readers(case, key)


def resolve(paths, *, sets=None) -> dict:
    analysis = analyze(paths, sets)
    gate(analysis.problems)
    return analysis.data


def layers(paths, *, sets=None) -> str:
    layer, _, _, overrides, _ = _layered(paths, sets)
    return describe_layers(layer, overrides)


def load_plugins(paths) -> list[Problem]:
    layer, _, _, _, problems = _layered(paths, None)
    _extend_sys_path(layer.walk())
    data, _, _, _ = merge_layers(layer)
    _, plugin_problems = _import_plugins(data.get("plugins", []))
    return [*problems, *plugin_problems]


def run(paths, *, sets=None, inputs=None, sinks=None, record_dir=None, executor="serial",
        workers=None):
    """Compile the recipe, build its components and run the flow on tezgah.

    ``inputs`` are the root bus keys the flow reads but does not produce;
    ``sinks`` receive tezgah's events. The params section never reaches the
    bus, it only feeds ``$name$`` placeholders.
    """
    analysis = analyze(paths, sets)
    gate(analysis.problems)
    if not isinstance(analysis.data.get("flow"), dict):
        raise CirakError("recipe has no flow section to run")
    given = dict(inputs or {})
    _run_setup(analysis.data, global_registry)
    pipeline, problems = compile_checked(analysis, list(given), dry=False)
    gate(problems)
    for sink in sinks or []:
        subscribe(pipeline, sink)
    if record_dir is not None:
        write_resolved(analysis, record_dir)
        write_flow(analysis, pipeline.resolved, record_dir)
    return tezgah_run(pipeline, inputs=given, executor=executor, workers=workers,
                      record_dir=record_dir)


def _run_setup(data, registry) -> None:
    for step in data.get("setup") or []:
        params = step.get("params")
        params = _plain_params(params) if isinstance(params, dict) else {}
        registry.resolve(step["uri"])(**params)


def _plain_params(value):
    if isinstance(value, dict):
        return {key: _plain_params(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_params(item) for item in value]
    if isinstance(value, str) and value.startswith("@@"):
        return value[1:]
    return value


def gate(problems) -> None:
    failures = [problem for problem in problems if problem.severity == "error"]
    warns = [problem for problem in problems if problem.severity == "warning"]
    if warns:
        warnings.warn(CirakWarning(render_problems(warns)), stacklevel=2)
    if failures:
        raise ConfigError(failures)


def _import_plugins(names) -> tuple[bool, list[Problem]]:
    problems: list[Problem] = []
    imported_new = False
    if not isinstance(names, list):
        problems.append(error("parse_error", "plugins must be a list of module names"))
        return imported_new, problems
    for name in names:
        if not isinstance(name, str):
            problems.append(error("plugin_import_failed",
                                  f"plugin entries must be strings, got {name!r}"))
        elif name not in sys.modules:
            try:
                import_module(name)
                imported_new = True
            except Exception as exc:
                problems.append(error("plugin_import_failed",
                                      f"cannot import plugin {name}: {exc}"))
    return imported_new, problems


def _extend_sys_path(files) -> None:
    for loaded in files:
        if loaded.file == "--set":
            continue
        directory = str(Path(loaded.file).parent)
        if directory not in sys.path:
            sys.path.insert(0, directory)


def _tezgah_problems(exc, provenance) -> list[Problem]:
    texts = [str(item) for item in getattr(exc, "problems", None) or [exc]]
    return [error("tezgah_validation", text, _flow_source(text, provenance))
            for text in texts]


def _flow_source(text, provenance):
    for name in re.findall(r"'([^']+)'", text):
        for path, source in provenance.items():
            if path and path[0] == "flow" and path[-1] == name:
                return source
    return None


def annotated(data, overrides) -> CommentedMap:
    """The recipe as a YAML tree with a comment on every overridden leaf."""
    losers: dict[tuple, list[Source]] = {}
    for path, _, loser in overrides:
        losers.setdefault(path, []).append(loser)
    winners = {path: winner for path, winner, _ in overrides}
    return _annotate(data, (), losers, winners)


def _annotate(value, path, losers, winners):
    if isinstance(value, dict):
        node = CommentedMap()
        for key, item in value.items():
            child = path + (key,)
            node[key] = _annotate(item, child, losers, winners)
            if child in losers:
                lost = ", ".join(str(source) for source in losers[child])
                node.yaml_add_eol_comment(f"{_source_text(winners[child])} overrides {lost}", key)
        return node
    if isinstance(value, list):
        node = CommentedSeq()
        for index, item in enumerate(value):
            node.append(_annotate(item, path + (index,), losers, winners))
        return node
    return value


def _source_text(source) -> str:
    return source.file if source.file == "--set" else str(source)


def flow_dump(paths, *, sets=None, inputs=None) -> str:
    """The dump document as YAML: component tables, graph blocks and the expanded flow with tezgah's resolution."""
    analysis = analyze(paths, sets)
    gate(analysis.problems)
    if not isinstance(analysis.flow, dict):
        raise CirakError("recipe has no flow section to show")
    pipeline, problems = compile_checked(analysis, _input_names(inputs), dry=True)
    plan = pipeline.resolved if pipeline is not None and not problems else None
    return dump_text(dump_document(analysis, plan))


def dump_document(analysis, plan) -> CommentedMap:
    """Three sections: ``components`` (every non reserved top level section of the resolved recipe),
    ``blocks`` (the spec and graph blocks, kept as templates; flow blocks are opened in the flow) and
    ``flow`` (the expanded flow, outputs written out, annotated with what tezgah resolved)."""
    from .expand import RESERVED

    document = CommentedMap()
    components = CommentedMap()
    for key, value in analysis.data.items():
        if key not in RESERVED:
            components[key] = _plain_tree(value)
    document["components"] = components
    blocks = CommentedMap()
    for name, block in (analysis.data.get("blocks") or {}).items():
        if not (isinstance(block, dict) and isinstance(block.get("flow"), dict)):
            blocks[name] = _plain_tree(block)
    document["blocks"] = blocks
    document["flow"] = annotated_flow(analysis.flow, plan)
    return document


def dump_text(tree) -> str:
    yaml = YAML()
    yaml.width = 160
    from io import StringIO
    stream = StringIO()
    yaml.dump(tree, stream)
    return stream.getvalue()


def annotated_flow(flow, plan) -> CommentedMap:
    """The expanded flow with a comment per node: implicit bindings, patterns, derived reads and exports."""
    return _annotate_pipeline(effective_flow(flow), plan)


def effective_flow(flow, registry=None):
    """The expanded flow with every step's outputs written out, facts and defaults included.

    A dump written this way reads and runs without the registry's facts.
    """
    registry = registry if registry is not None else global_registry
    return _effective_pipeline(flow, registry)


def _effective_pipeline(spec, registry):
    result = {}
    for key, value in spec.items():
        result[key] = value if key in PIPELINE_FIELDS else _effective_node(value, key, registry)
    return result


def _effective_node(value, name, registry):
    if not isinstance(value, dict):
        return value
    if "uri" in value or ("block" in value and "builder" in value):
        uri = value["uri"] if "uri" in value else value["builder"]
        outputs, unpack = step_outputs(value, name, registry.facts(uri))
        node = dict(value)
        node["outputs"] = outputs
        if unpack and isinstance(outputs, list):
            node["unpack"] = True
        return node
    marker = next((marker for marker in MARKERS if marker in value), None)
    if marker is None:
        return _effective_pipeline(value, registry)
    inside = value[marker]
    if not isinstance(inside, dict):
        return value
    copy = {}
    for field, item in inside.items():
        if field in ("body", "default") and isinstance(item, dict):
            copy[field] = _effective_node(item, field, registry)
        elif field == "cases" and isinstance(item, dict):
            copy[field] = {label: _effective_node(case, str(label), registry) for label, case in item.items()}
        else:
            copy[field] = item
    return {marker: copy}


PIPELINE_FIELDS = ("inputs", "outputs", "wait_for")
MARKERS = ("map", "loop", "branch")


def _annotate_pipeline(spec, entry):
    node = CommentedMap()
    table = getattr(entry, "table", None) or {}
    for key, value in spec.items():
        if key in PIPELINE_FIELDS:
            node[key] = _plain_tree(value)
            continue
        node[key] = _annotate_node(value, table.get(key))
        comment = _node_comment(value, table.get(key))
        if comment:
            node.yaml_add_eol_comment(comment, key)
    return node


def _annotate_node(value, entry):
    if not isinstance(value, dict):
        return _plain_tree(value)
    if "uri" in value or "block" in value:
        return _plain_tree(value)
    marker = next((marker for marker in MARKERS if marker in value), None)
    if marker is None:
        return _annotate_pipeline(value, entry)
    inside = value[marker]
    if not isinstance(inside, dict):
        return _plain_tree(value)
    copy = CommentedMap()
    for field, item in inside.items():
        if field == "body" and isinstance(item, dict):
            body_entry = getattr(entry, "body", None)
            copy[field] = _annotate_node(item, body_entry)
            comment = _node_comment(item, body_entry)
            if comment:
                copy.yaml_add_eol_comment(comment, field)
        elif field == "cases" and isinstance(item, dict):
            cases = CommentedMap()
            case_entries = getattr(entry, "cases", None) or {}
            for label, case in item.items():
                cases[label] = _annotate_node(case, case_entries.get(label))
            copy[field] = cases
        elif field == "default" and isinstance(item, dict):
            copy[field] = _annotate_node(item, getattr(entry, "default", None))
        else:
            copy[field] = _plain_tree(item)
    holder = CommentedMap()
    holder[marker] = copy
    return holder


def _node_comment(value, entry):
    if entry is None or not isinstance(value, dict):
        return None
    parts = []
    kind = getattr(entry, "kind", None)
    if kind == "step":
        for param, pattern in entry.patterns.items():
            matched = entry.binding.get(param)
            keys = ", ".join(matched) if isinstance(matched, dict) else str(matched)
            parts.append(f"{param} {pattern}: {keys}")
        implicit = [key for key in entry.implicit.values() if isinstance(key, str)]
        implicit += [", ".join(keys) for keys in entry.implicit.values() if isinstance(keys, list)]
        if implicit:
            parts.append("implicit: " + ", ".join(implicit))
        when_implicit = getattr(entry, "when_implicit", None) or {}
        if when_implicit:
            parts.append("when implicit: " + ", ".join(str(key) for key in when_implicit.values()))
        if entry.passthrough:
            parts.append("passthrough: " + ", ".join(entry.passthrough))
    elif kind == "pipeline":
        if entry.derived_inputs and entry.inputs:
            parts.append("reads: " + ", ".join(entry.inputs))
        if entry.derived_outputs and entry.outputs:
            parts.append("exports: " + ", ".join(entry.outputs))
    elif kind in ("loop", "map"):
        if entry.broadcast:
            parts.append("reads: " + ", ".join(entry.broadcast))
        until_implicit = getattr(entry, "until_implicit", None) or {}
        if until_implicit:
            parts.append("until implicit: " + ", ".join(str(key) for key in until_implicit.values()))
    elif kind == "branch":
        implicit = getattr(entry, "implicit", None) or {}
        if implicit:
            parts.append("decide implicit: " + ", ".join(str(key) for key in implicit.values()))
    return "; ".join(parts) if parts else None


def _plain_tree(value):
    """A YAML tree where lists and lego records (mappings with uri or block) are written inline."""
    if isinstance(value, dict):
        node = CommentedMap()
        for key, item in value.items():
            node[key] = _plain_tree(item)
        if "uri" in value or "block" in value or "model" in value:
            node.fa.set_flow_style()
        return node
    if isinstance(value, list):
        node = CommentedSeq()
        for item in value:
            node.append(_plain_tree(item))
        if not any(isinstance(item, (dict, list)) for item in value):
            node.fa.set_flow_style()
        return node
    return value


def write_flow(analysis, plan, record_dir) -> None:
    target = Path(record_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "flow.yaml").write_text(dump_text(dump_document(analysis, plan)))


def write_resolved(analysis, record_dir) -> None:
    target = Path(record_dir)
    target.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    with (target / "resolved.yaml").open("w") as stream:
        yaml.dump(annotated(analysis.data, analysis.overrides), stream)
