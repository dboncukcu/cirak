from .registry import Registry, registry


def compose(graph):
    def composed(*args):
        values = dict(zip(graph.inputs, args))
        for node in graph.nodes:
            result = node.obj(*[values[key] for key in node.inputs])
            if len(node.outputs) == 1:
                values[node.outputs[0]] = result
            else:
                values.update(zip(node.outputs, result))
        if len(graph.outputs) == 1:
            return values[graph.outputs[0]]
        return tuple(values[key] for key in graph.outputs)
    return composed


def register_std(target: Registry) -> None:
    target.register("/builder/cirak/compose", compose,
                    description="Chain a graph into one plain callable, running nodes in order")


register_std(registry)
