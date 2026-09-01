import functools

from tezgah import Branch, Loop, Map, Pipeline, Step

from .errors import CirakError

PIPELINE_FIELDS = ("inputs", "outputs", "wait_for")


def compile_flow(data, store, registry) -> Pipeline:
    flow = data.get("flow")
    if not isinstance(flow, dict):
        raise CirakError("recipe has no flow section to run")
    return _pipeline(flow, "flow", store, registry)


def _pipeline(spec, name, store, registry) -> Pipeline:
    nodes = [_node(child, key, store, registry)
             for key, child in spec.items() if key not in PIPELINE_FIELDS]
    return Pipeline(nodes,
                    inputs=spec.get("inputs"),
                    outputs=spec.get("outputs"),
                    name=name,
                    wait_for=spec.get("wait_for"))


def _node(spec, name, store, registry):
    if not isinstance(spec, dict):
        raise CirakError(f"{name}: not a valid flow node")
    if "uri" in spec:
        return _step(spec, name, store, registry)
    if "map" in spec:
        inside = spec["map"]
        return Map(body=_body(inside, name, "map", store, registry),
                   over=inside.get("over"),
                   item=inside.get("item", "item"),
                   index=inside.get("index"),
                   collect=inside.get("collect"),
                   parallel=inside.get("parallel", False),
                   name=name,
                   wait_for=inside.get("wait_for"))
    if "loop" in spec:
        inside = spec["loop"]
        return Loop(body=_body(inside, name, "loop", store, registry),
                    carry=inside.get("carry"),
                    range=inside.get("range"),
                    index=inside.get("index"),
                    until=_condition(inside.get("until"), store, registry),
                    trace=inside.get("trace"),
                    outputs=inside.get("outputs"),
                    name=name,
                    wait_for=inside.get("wait_for"))
    if "branch" in spec:
        inside = spec["branch"]
        cases = {label: _node(case, str(label), store, registry)
                 for label, case in inside.get("cases", {}).items()}
        default = inside.get("default")
        return Branch(decide=_condition(inside.get("decide"), store, registry),
                      inputs=inside.get("inputs"),
                      cases=cases,
                      default=None if default is None else _node(default, "default", store, registry),
                      name=name,
                      wait_for=inside.get("wait_for"))
    return _pipeline(spec, name, store, registry)


def _body(inside, name, marker, store, registry):
    body = inside.get("body")
    if body is None:
        raise CirakError(f"{name}: {marker} needs a body")
    return _node(body, "body", store, registry)


def _step(spec, name, store, registry) -> Step:
    fn = _bound(spec["uri"], spec.get("params"), store, registry)
    return Step(fn,
                inputs=spec.get("inputs", []),
                outputs=spec.get("outputs", []),
                name=name,
                when=_condition(spec.get("when"), store, registry),
                wait_for=spec.get("wait_for"),
                retries=spec.get("retries", 0),
                wait=spec.get("wait", 0.0))


def _condition(spec, store, registry):
    if spec is None:
        return None
    if isinstance(spec, str):
        return registry.resolve(spec)
    return _bound(spec.get("uri"), spec.get("params"), store, registry)


def _bound(uri, params, store, registry):
    fn = registry.resolve(uri)
    if isinstance(params, dict) and params:
        fn = functools.partial(fn, **store.resolve_params(params))
    return fn
