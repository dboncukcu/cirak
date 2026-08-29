import functools
from dataclasses import dataclass

from .errors import BuildError, dotted
from .expand import components
from .registry import registry as default_registry


@dataclass(frozen=True)
class GraphNode:
    name: str
    obj: object
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class Graph:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    nodes: tuple[GraphNode, ...]


class ComponentStore:
    def __init__(self, data, expansions, registry):
        self._components = {dotted(path): component for path, component in components(data)}
        self._blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
        self._expansions = expansions
        self._registry = registry
        self._built: dict[str, object] = {}
        self._group_prefixes: set[str] = set()
        for key in self._components:
            parts = key.split(".")
            for cut in range(1, len(parts)):
                self._group_prefixes.add(".".join(parts[:cut]))

    def get(self, ref: str):
        if ref in self._built:
            return self._built[ref]
        if ref in self._components:
            value = self._build_component(ref, self._components[ref])
        elif ref in self._group_prefixes:
            value = self._build_group(ref)
        else:
            raise BuildError(f"@{ref} does not match any component")
        self._built[ref] = value
        return value

    def _build_group(self, prefix: str) -> dict:
        segments: dict[str, None] = {}
        for key in self._components:
            if key.startswith(prefix + "."):
                segments.setdefault(key[len(prefix) + 1:].split(".")[0], None)
        return {segment: self.get(f"{prefix}.{segment}") for segment in segments}

    def _build_component(self, name, component):
        try:
            if "uri" in component:
                params = component.get("params")
                params = params if isinstance(params, dict) else {}
                return self._prepare(component["uri"], params, bool(component.get("partial")))
            expansion = self._expansions[name]
            nodes = []
            for node_name in _ordered(expansion):
                node = expansion["graph"][node_name]
                obj = self._prepare(node["uri"], node["params"], node["partial"])
                nodes.append(GraphNode(node_name, obj, tuple(node["inputs"]), tuple(node["outputs"])))
            graph = Graph(tuple(expansion["inputs"]), tuple(expansion["outputs"]), tuple(nodes))
            definition = self._blocks.get(component.get("block"))
            definition = definition if isinstance(definition, dict) else {}
            builder_uri = component.get("builder", definition.get("builder"))
            builder = self._registry.resolve(builder_uri)
            return builder(graph)
        except BuildError:
            raise
        except Exception as exc:
            raise BuildError(f"building component {name} failed: {exc}") from exc

    def _prepare(self, uri, params, partial):
        target = self._registry.resolve(uri)
        resolved = self.resolve_params(params)
        if partial:
            return functools.partial(target, **resolved) if resolved else target
        return target(**resolved)

    def resolve_params(self, value):
        if isinstance(value, dict):
            return {key: self.resolve_params(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_params(item) for item in value]
        if isinstance(value, str):
            if value.startswith("@@"):
                return value[1:]
            if value.startswith("@"):
                return self.get(value[1:])
        return value


def _ordered(expansion) -> list[str]:
    producer = {}
    for name, node in expansion["graph"].items():
        for wire in node["outputs"]:
            producer[wire] = name
    deps = {name: [producer[wire] for wire in node["inputs"] if wire in producer]
            for name, node in expansion["graph"].items()}
    order: list[str] = []
    done: set[str] = set()

    def visit(name):
        if name in done:
            return
        done.add(name)
        for dep in deps[name]:
            visit(dep)
        order.append(name)

    for name in expansion["graph"]:
        visit(name)
    return order


def build_components(data, expansions, registry=None) -> ComponentStore:
    return ComponentStore(data, expansions, registry if registry is not None else default_registry)
