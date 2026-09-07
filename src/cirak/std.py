import warnings

from .errors import CirakWarning
from .registry import Registry, registry


def spread(name, result, outputs):
    if not isinstance(result, (tuple, list)):
        raise TypeError(f"node {name!r} declares unpack but returned {type(result).__name__}, "
                        f"not a tuple or list")
    if len(result) < len(outputs):
        raise ValueError(f"node {name!r} declares {len(outputs)} outputs {list(outputs)} but "
                         f"returned {len(result)} values")
    if len(result) > len(outputs):
        warnings.warn(f"node {name!r} returned {len(result)} values but declares "
                      f"{len(outputs)} outputs {list(outputs)}; the rest are dropped",
                      CirakWarning, stacklevel=3)
    return dict(zip(outputs, result))


def compose(graph):
    for node in graph.nodes:
        if node.ref is not None:
            raise TypeError(f"compose cannot build node {node.name!r}: it references {node.ref!r}, "
                            f"an object only a run time builder can supply")

    def composed(*args):
        values = dict(zip(graph.inputs, args))
        for node in graph.nodes:
            result = node.obj(*[values[key] for key in node.inputs])
            if node.unpack:
                values.update(spread(node.name, result, node.outputs))
            else:
                values[node.outputs[0]] = result
        if len(graph.outputs) == 1:
            return values[graph.outputs[0]]
        return tuple(values[key] for key in graph.outputs)
    return composed


def register_std(target: Registry) -> None:
    target.register("/builder/cirak/compose", compose,
                    description="Chain a graph into one plain callable, running nodes in order")


register_std(registry)
