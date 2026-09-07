import functools
from dataclasses import dataclass, field

from .errors import BuildError, dotted
from .expand import components, empty_groups
from .registry import registry as default_registry


@dataclass(frozen=True)
class GraphNode:
    """One node of a compiled graph.

    ``obj`` is the constructed object, or None when the node is a reference
    (``ref``) to an object the builder receives at run time. ``extra`` carries
    the keys cirak passes through untouched, ``init`` among them.
    """

    name: str
    obj: object
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    unpack: bool = False
    ref: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Graph:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    nodes: tuple[GraphNode, ...]


class Deferred:
    """A ``kind: data`` component: resolved but not built, the caller builds it when its data is ready."""

    def __init__(self, uri, params, target):
        self.uri = uri
        self.params = params
        self.target = target

    def build(self, **extra):
        return self.target(**self.params, **extra)

    def __repr__(self):
        return f"Deferred({self.uri!r}, {self.params!r})"


class Placeholder:
    """Stands in for a built object while a recipe is checked without building."""

    def __init__(self, ref):
        self.ref = ref

    def __repr__(self):
        return f"Placeholder({self.ref!r})"

    def __call__(self, *args, **kwargs):
        raise BuildError(f"{self!r} was called: a checked recipe must not run anything")


class ComponentStore:
    """Builds components on demand; ``dry`` hands out placeholders instead."""

    def __init__(self, data, expansions, registry, dry=False):
        self._components = {dotted(path): component for path, component in components(data)}
        self._blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
        self._expansions = expansions
        self._registry = registry
        self.dry = dry
        self._built: dict[str, object] = {}
        self._group_prefixes: set[str] = set(empty_groups(data))
        for key in self._components:
            parts = key.split(".")
            for cut in range(1, len(parts)):
                self._group_prefixes.add(".".join(parts[:cut]))

    def expansion(self, key: str):
        return self._expansions.get(key)

    def get(self, ref: str):
        if ref in self._built:
            return self._built[ref]
        if ref in self._components:
            value = Placeholder(ref) if self.dry else self._build_component(ref, self._components[ref])
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
                partial = component.get("partial", self._registry.facts(component["uri"]).partial)
                return self._prepare(component["uri"], params, bool(partial))
            expansion = self._expansions[name]
            nodes = []
            graph = self.graph(expansion)
            definition = self._blocks.get(component.get("block"))
            definition = definition if isinstance(definition, dict) else {}
            builder_uri = component.get("builder", definition.get("builder"))
            builder = self._registry.resolve(builder_uri)
            return builder(graph)
        except BuildError:
            raise
        except Exception as exc:
            raise BuildError(f"building component {name} failed: {exc}") from exc

    def _inline(self, value):
        uri = value["uri"]
        params = value.get("params")
        params = params if isinstance(params, dict) else {}
        facts = self._registry.facts(uri)
        if facts.kind == "data":
            target = self._registry.resolve(uri)
            return Deferred(uri, self.resolve_params(params), target)
        return self._prepare(uri, params, bool(value.get("partial", facts.partial)))

    def graph(self, expansion) -> "Graph":
        """Construct every node object of an expansion; reference nodes stay unbuilt."""
        nodes = []
        for node_name in _ordered(expansion):
            node = expansion["graph"][node_name]
            if "ref" in node:
                nodes.append(GraphNode(node_name, None, tuple(node["inputs"]), tuple(node["outputs"]),
                                       bool(node.get("unpack", False)), ref=node["ref"]))
                continue
            obj = self._prepare(node["uri"], node["params"], node["partial"])
            extra = {"init": node["init"]} if "init" in node else {}
            nodes.append(GraphNode(node_name, obj, tuple(node["inputs"]), tuple(node["outputs"]),
                                   bool(node.get("unpack", False)), extra=extra))
        return Graph(tuple(expansion["inputs"]), tuple(expansion["outputs"]), tuple(nodes))

    def _prepare(self, uri, params, partial):
        target = self._registry.resolve(uri)
        resolved = self.resolve_params(params)
        if self.dry:
            return Placeholder(uri)
        if partial:
            return functools.partial(target, **resolved) if resolved else target
        return target(**resolved)

    def resolve_params(self, value):
        if isinstance(value, dict):
            if isinstance(value.get("uri"), str):
                return self._inline(value)
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


def build_components(data, expansions, registry=None, dry=False) -> ComponentStore:
    return ComponentStore(data, expansions, registry if registry is not None else default_registry, dry)
