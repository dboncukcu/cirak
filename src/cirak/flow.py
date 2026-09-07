import functools
import inspect

from tezgah import Branch, Loop, Map, Pipeline, Step

from .errors import CirakError, dotted
from .registry import UNSET

PIPELINE_FIELDS = ("inputs", "outputs", "wait_for")


def compile_flow(data, store, registry) -> Pipeline:
    """Compile an expanded flow onto tezgah nodes; ``data`` is the recipe with the expanded flow in place."""
    flow = data.get("flow")
    if not isinstance(flow, dict):
        raise CirakError("recipe has no flow section to run")
    return _pipeline(flow, "flow", ("flow",), store, registry)


def _pipeline(spec, name, path, store, registry) -> Pipeline:
    nodes = [_node(child, key, path + (key,), store, registry)
             for key, child in spec.items() if key not in PIPELINE_FIELDS]
    return Pipeline(nodes,
                    inputs=spec.get("inputs"),
                    outputs=spec.get("outputs"),
                    name=name,
                    wait_for=spec.get("wait_for"))


def _node(spec, name, path, store, registry):
    if not isinstance(spec, dict):
        raise CirakError(f"{name}: not a valid flow node")
    if "uri" in spec:
        return _step(spec, name, store, registry)
    if "block" in spec and "builder" in spec:
        return _builder_step(spec, name, path, store, registry)
    if "map" in spec:
        inside = spec["map"]
        return Map(body=_body(inside, name, "map", path, store, registry),
                   over=inside.get("over"),
                   item=inside.get("item", "item"),
                   index=inside.get("index"),
                   collect=inside.get("collect"),
                   parallel=inside.get("parallel", False),
                   name=name,
                   wait_for=inside.get("wait_for"))
    if "loop" in spec:
        inside = spec["loop"]
        until, until_bus = _key_or_predicate(inside.get("until"), store, registry)
        return Loop(body=_body(inside, name, "loop", path, store, registry),
                    carry=inside.get("carry"),
                    range=inside.get("range"),
                    index=inside.get("index"),
                    until=until,
                    until_bus=until_bus,
                    trace=inside.get("trace"),
                    outputs=inside.get("outputs"),
                    next=inside.get("next"),
                    name=name,
                    wait_for=inside.get("wait_for"))
    if "branch" in spec:
        inside = spec["branch"]
        cases = {label: _node(case, str(label), path + ("branch", "cases", label), store, registry)
                 for label, case in inside.get("cases", {}).items()}
        default = inside.get("default")
        decide, decide_bus = _predicate(inside.get("decide"), store, registry)
        return Branch(decide=decide,
                      inputs=inside.get("inputs"),
                      cases=cases,
                      default=None if default is None else _node(default, "default", path + ("branch", "default"),
                                                                 store, registry),
                      bus=decide_bus,
                      name=name,
                      wait_for=inside.get("wait_for"))
    return _pipeline(spec, name, path, store, registry)


def _body(inside, name, marker, path, store, registry):
    body = inside.get("body")
    if body is None:
        raise CirakError(f"{name}: {marker} needs a body")
    return _node(body, "body", path + (marker, "body"), store, registry)


def _builder_step(spec, name, path, store, registry) -> Step:
    """A step that calls ``builder(graph, **params, **inputs)`` at run time on a graph compiled now."""
    key = dotted(path)
    expansion = store.expansion(key)
    if expansion is None:
        raise CirakError(f"{key}: block {spec.get('block')!r} was not expanded")
    graph = store.graph(expansion)
    builder = registry.resolve(spec["builder"])
    facts = registry.facts(spec["builder"])
    params = spec.get("params")
    given = store.resolve_params(params) if isinstance(params, dict) and params else {}
    fn = functools.partial(builder, graph, **given)
    fn.__name__ = getattr(builder, "__name__", "builder")
    inputs = spec.get("inputs")
    if inputs is None:
        inputs = _required_names(fn, given)
    outputs, unpack = step_outputs(spec, name, facts)
    when, when_bus = _key_or_predicate(spec.get("when"), store, registry)
    passthrough = spec.get("passthrough")
    if passthrough is None:
        passthrough = when is not None and bool(outputs) and covered(outputs, facts)
    return Step(fn,
                inputs=inputs,
                outputs=outputs,
                name=name,
                when=when,
                when_bus=when_bus,
                wait_for=spec.get("wait_for"),
                retries=spec.get("retries", 0),
                wait=spec.get("wait", 0.0),
                unpack=unpack,
                bus=implicit_bus(facts, given, inputs) or None,
                passthrough=passthrough)


def _step(spec, name, store, registry) -> Step:
    fn = registry.resolve(spec["uri"])
    facts = registry.facts(spec["uri"])
    params = spec.get("params")
    given = store.resolve_params(params) if isinstance(params, dict) and params else {}
    inputs = spec.get("inputs")
    if inputs is None:
        inputs = _required_names(fn, given)
    outputs, unpack = step_outputs(spec, name, facts)
    when, when_bus = _key_or_predicate(spec.get("when"), store, registry)
    passthrough = spec.get("passthrough")
    if passthrough is None:
        passthrough = when is not None and bool(outputs) and covered(outputs, facts)
    return Step(functools.partial(fn, **given) if given else fn,
                inputs=inputs,
                outputs=outputs,
                name=name,
                when=when,
                when_bus=when_bus,
                wait_for=spec.get("wait_for"),
                retries=spec.get("retries", 0),
                wait=spec.get("wait", 0.0),
                unpack=unpack,
                bus=implicit_bus(facts, given, inputs) or None,
                passthrough=passthrough)


def step_outputs(spec, name, facts):
    """The outputs a flow step writes and whether it unpacks its return value.

    YAML wins; without it the lego's ``returns`` fact decides; a lego that says
    nothing writes its whole return value under the step's own name.
    """
    if "outputs" in spec:
        outputs = spec["outputs"]
        unpack = spec.get("unpack")
        if isinstance(outputs, str):
            outputs = [outputs]
        if isinstance(outputs, dict):
            return outputs, None
        return outputs, bool(unpack)
    if facts.returns is UNSET:
        return [name], bool(spec.get("unpack", False))
    if facts.returns is None:
        return [], False
    if isinstance(facts.returns, str):
        return [facts.returns], False
    return list(facts.returns), True


def output_names(outputs, unpack):
    """The names tezgah matches against bound inputs for passthrough."""
    if isinstance(outputs, dict):
        return list(outputs)
    return list(outputs)


def covered(outputs, facts) -> bool:
    names = output_names(outputs, None)
    given_back = set(facts.mutates) | set(facts.aliases)
    return bool(names) and all(name in given_back for name in names)


def implicit_bus(facts, given, inputs) -> dict:
    bound = set(given)
    if isinstance(inputs, dict):
        bound.update(inputs)
    elif isinstance(inputs, (list, tuple)):
        bound.update(inputs)
    return {param: key for param, key in facts.bus.items() if param not in bound}


def _required_names(fn, given):
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    return [parameter.name for parameter in signature.parameters.values()
            if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
            and parameter.default is parameter.empty and parameter.name not in given]


def _key_or_predicate(spec, store, registry):
    """A bus key stays a key; a ``{uri, params}`` mapping becomes a predicate with its implicit bindings."""
    if spec is None or isinstance(spec, str):
        return spec, None
    return _bound(spec.get("uri"), spec.get("params"), store, registry)


def _predicate(spec, store, registry):
    if spec is None:
        return None, None
    if isinstance(spec, str):
        return _bound(spec, None, store, registry)
    return _bound(spec.get("uri"), spec.get("params"), store, registry)


def _bound(uri, params, store, registry):
    """The predicate callable and the ``bus`` fact tezgah binds its defaulted parameters with.

    Parameters given in ``params`` are fixed with functools.partial; tezgah sees
    them as defaulted and never looks them up (T12).
    """
    fn = registry.resolve(uri)
    given = store.resolve_params(params) if isinstance(params, dict) and params else {}
    bus = implicit_bus(registry.facts(uri), given, None) or None
    return (functools.partial(fn, **given) if given else fn), bus
