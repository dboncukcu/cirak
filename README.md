# çırak

**A YAML front end for Python pipelines: describe your computation as recipes assembled from registered building blocks, catch every configuration error before anything runs, then execute on [tezgah](https://github.com/dboncukcu/tezgah).**

çırak is a compiler, not a runtime. It reads declarative YAML recipes layered through `include`, resolves every referenced building block through a registry, validates the whole configuration in one pass (its own rules and tezgah's), constructs the Python objects, and hands the assembled pipeline to tezgah for execution. The division of labor is strict: çırak never executes anything, tezgah never parses anything.

```
you (the master)   write the recipe (YAML)
        │
      çırak        compiles: resolve, validate, construct, translate
        │
      tezgah       executes: ordering, parallelism, events, records
```

The names follow the Turkish guild tradition. The tezgah is the workbench. The çırak is the apprentice who assembles parts on that bench by following a written recipe. The usta, the master who writes the recipe, is you. çırak itself contains no domain knowledge and never will; domain catalogs (for machine learning or anything else) are ordinary packages layered on top.

## Table of contents

- [Why](#why)
- [Install](#install)
- [Five minute tour](#five-minute-tour)
- [Core concepts](#core-concepts)
- [The registry](#the-registry)
- [Lego facts](#lego-facts)
- [The value language](#the-value-language)
- [Blocks](#blocks)
- [Builders](#builders)
- [Flow](#flow)
- [Flow blocks and foreach](#flow-blocks-and-foreach)
- [Setup](#setup)
- [Layers and merging](#layers-and-merging)
- [Validation](#validation)
- [Error model](#error-model)
- [Command line](#command-line)
- [Records and reproducibility](#records-and-reproducibility)
- [Python API](#python-api)
- [Development](#development)

## Why

Configuration layers around pipeline code tend to rot the same way in every project: a hand rolled YAML loader, values stitched together through ad hoc overrides, a typo that surfaces forty minutes into a long run, and results that nobody can reproduce a month later. çırak makes five bets against that:

1. **Everything is a building block.** Functions, classes, builders, even packaged YAML fragments are registered under URIs and assembled declaratively. Swapping an implementation means swapping a URI, not editing code. A building block may declare facts about itself (what it returns, what it reads from the bus, what it changes in place), and the recipe then needs less wiring.
2. **One grammar.** There are seven reserved keys; every other key follows a single component grammar. File boundaries carry no meaning: a recipe may live in one file or twenty, and any section may appear in any file.
3. **Layers, not patches.** Inside a layer merging is strict: the same key defined twice is an error, even when the values are equal. Between layers the upper one wins leaf by leaf, and the layers are the `include` tree itself, so every override has one visible source and the record names it.
4. **All problems at once, before anything runs.** Unknown URIs (with suggestions), missing variables, signature mismatches, graph cycles, dangling references, and everything tezgah finds in the compiled pipeline: collected across the whole recipe and reported together, each with file and line.
5. **A run's record reproduces it.** Every recorded run contains a `resolved.yaml` that is itself a valid recipe, and a `flow.yaml` that shows the graph that ran. One file is enough to run the same thing again.

## Install

Requires Python 3.13 or newer. The dependencies are tezgah 0.2 (which itself has none) and ruamel.yaml.

```
pip install cirak
```

From source:

```
git clone https://github.com/dboncukcu/cirak.git
cd cirak
uv sync
uv run cirak --help
```

## Five minute tour

Building blocks are ordinary Python, registered under URIs. Descriptions feed `cirak ls` and `cirak search`; write your own, or skip it and çırak derives one from the name and signature.

```python
import csv
from pathlib import Path

import cirak


@cirak.register("/io/demo/read_csv", description="Read a CSV file into a list of row dicts")
def read_csv(path):
    with Path(path).open(newline="") as stream:
        return list(csv.DictReader(stream))


@cirak.register("/table/demo/column", description="Pick one numeric column as a list of floats")
def column(rows, name):
    return [float(row[name]) for row in rows]


@cirak.register("/series/demo/rolling_mean", description="Rolling mean over a window")
def rolling_mean(values, window):
    out = []
    for position in range(len(values)):
        start = max(0, position - window + 1)
        chunk = values[start:position + 1]
        out.append(sum(chunk) / len(chunk))
    return out


@cirak.register("/series/demo/apply", description="Apply a callable to a series")
def apply(fn, values):
    return fn(values)


@cirak.register("/stat/demo/mean", description="Arithmetic mean of a series")
def mean(values):
    return sum(values) / len(values)
```

Save that as `parts.py` next to your recipe. The recipe, `report.yaml`, wires the blocks together:

```yaml
plugins: [parts]

params:
  input: data/sales.csv
  smoothing_passes: 2

blocks:
  smooth_chain:
    variables:
      window: {default: 3}
      passes: {required: true}
    spec:
      - uri: /series/demo/rolling_mean
        partial: true
        params: {window: $window$}
        repeat: $passes$

smoother:
  block: smooth_chain
  params: {passes: $smoothing_passes$, window: 4}
  builder: /builder/cirak/compose

flow:
  outputs: [average]
  load: {uri: /io/demo/read_csv, params: {path: $input$}, outputs: [rows]}
  pick: {uri: /table/demo/column, params: {name: amount}, inputs: [rows], outputs: [values]}
  smooth: {uri: /series/demo/apply, params: {fn: "@smoother"}, inputs: [values], outputs: [smoothed]}
  average: {uri: /stat/demo/mean, inputs: {values: smoothed}}
```

Point `input` at any small CSV with a numeric `amount` column, then check it, run it, keep the record:

```
cirak check report.yaml
cirak run report.yaml --record runs/first --set params.smoothing_passes=3
```

```
run r_7f3a2c9b: ok
outputs:
  average: 17.25
```

`runs/first/` now holds tezgah's event stream and run summary, plus `resolved.yaml`: the recipe with every alias and placeholder resolved and the `--set` override marked with a comment. It is a valid recipe in its own right:

```
cirak run runs/first/resolved.yaml
```

## Core concepts

| term | meaning |
|---|---|
| recipe | The configuration: a tree of YAML files, one layer per included file, folded into one document |
| layer | One file with the files it includes below it; strict inside, overriding between |
| registry | The catalog of building blocks (legos), addressed by URI |
| facts | What a lego declares about itself at registration: kind, returns, bus, mutates, and more |
| component | A named, buildable definition: `{uri or block, params, ...}` |
| group | A mapping that contains components; referenced as a whole or by member |
| block | A parameterized template: `variables` plus `spec`, `graph` or `flow` |
| graph | Named nodes connected by named wires, single assignment, acyclic |
| builder | A registered callable that turns a compiled graph into one object |
| flow | The root pipeline, compiled to tezgah and executed there |
| flow block | A block whose body is a piece of flow; used in a flow it opens into a transparent pipeline |
| foreach | One node template stamped once per element of a list or mapping, at expansion time |
| fragment | A registered YAML piece that recipes can include by URI |
| problem | One finding: severity, kind, message, file, line, hint |

A recipe has exactly seven reserved top level keys: `include`, `plugins`, `alias`, `params`, `blocks`, `setup`, `flow`. Every other top level key is a component or a group of components. çırak recognizes no section names beyond the seven; `metrics:` or `tools:` are just names you chose.

## The registry

URIs follow one scheme:

```
/kind/provider/name[/subname...]
```

At least three lowercase segments. The first names the kind (`table`, `series`, `builder`, `flow`), the second the provider, the rest is a free hierarchy. çırak attaches no semantics to the first segment; a lego's kind is a fact it declares (see below), the scheme exists for browsing and search.

Registration comes in four forms:

```python
cirak.register("/table/demo/dropna", dropna, description="Drop rows with missing values")

@cirak.register("/report/proj/weekly", kind="lego", returns="report", bus=["record"])
def weekly(rows, title, record=None): ...

cirak.register_many("/series/statlib", {
    "rolling_mean": ("statlib.series:rolling_mean", "Rolling mean over a window"),
    "diff": "statlib.series:diff",
})

cirak.register_fragment("/flow/proj/report", path, description="CSV to JSON summary flow")
```

`register` takes the facts as keyword arguments and works either way, as a call or as a decorator. A catalog that
wants its own vocabulary wraps it: kalfa registers with `@kalfa.lego`, which derives the kind from the first
segment of the URI and hands the rest to `register`.

Rules worth knowing:

1. Descriptions are optional but never absent. When you write one it wins; when you do not, çırak derives one: the name and signature for callables (`read_csv(path, headers: bool = True)`), the import string for lazy targets, the file path for fragments. Either way `ls` and `search` always have something real to show. Curated catalogs should still write their own.
2. String targets in the form `"module.path:name"` load lazily: registering a catalog imports nothing, the module is imported the first time the URI is actually built. Heavy libraries stay unloaded until a recipe uses them. Facts that need the signature are checked at that moment instead of at registration.
3. Registering the same URI again with an identical target and identical facts is silently ignored (double imports are harmless). A different target or different facts raises `RegistryError`.
4. Discovery is twofold. Modules listed in a recipe's `plugins:` section are imported at compile time, and the directories of the recipe files are placed on `sys.path` first, so a `parts.py` sitting next to the recipe is found without installing anything. Installed packages announce their catalogs through the `cirak.plugins` entry point group and are loaded when the command line starts.
5. çırak ships a small standard catalog, registered the moment you `import cirak`. Its only member today is `/builder/cirak/compose` (see [Builders](#builders)). The bar for entry is strict: domain free, zero dependencies, small.

Browse with `cirak ls /series` and `cirak search rolling`; `ls` prints each entry's kind and facts, and `--kind` filters.

## Lego facts

A fact is a fixed statement about a function: what it returns, which parameters it may read from the bus, which objects it changes in place. Facts live on the registry entry, çırak reads them at compile time and fills in what the recipe did not write. Facts never select behavior: the same lego does the same thing in every recipe, and a recipe that writes the field explicitly always wins over the fact. A lego that declares nothing works under the plain rules of this document.

| fact | form | meaning | checked at registration |
|---|---|---|---|
| `kind` | a declared kind | classification; `when`, `until` and `decide` targets must be `predicate`, `builder` fields must be `builder` (`kind_mismatch`); catalogs on top of çırak read the rest | declared with `declare_kinds` |
| `alias` | string or list | short names; `Registry.aliases()` collects them, one name maps to one URI | unique, no `/` |
| `description` | text | `ls` and `search` | |
| `returns` | `None`, string or list | default outputs of a flow step: `None` is a side effect, a string writes the whole value under that key, a list unpacks a returned mapping by those names | |
| `bus` | list or mapping | defaulted parameters that bind implicitly to a same named bus key (mapping form: a key or a pattern per parameter); the Python default stays when the key is absent | names exist and have defaults |
| `mutates` | list | objects from these parameters change in place and come back under the same name; with `when`, outputs covered by `mutates` (or `aliases`) get tezgah's `passthrough` | names in the signature; in `returns` when that is a list |
| `aliases` | string or list | parameters handed back unchanged | names in the signature |
| `partial` | bool | the component is not called at build time, the callable itself (with its params attached) is the component | |
| `state` | `True` or list | which parts of the return value are persistent state; stored, not interpreted | list names in `returns` |
| `refs` | mapping | parameters whose string values are references of the given type; stored for catalogs, çırak checks the names exist | names in the signature |
| a fact the catalog declared | anything | stored as written, never interpreted; `Facts.get(name)` reads it and `Facts.declared()` lists it (kalfa declares `uses`, `needs_grad`, `needs_models`, `extras`, `grouped` this way) | the name is declared |

çırak owns three kinds, the ones its own rules read: `builder`, `predicate` and `data`. A catalog declares the rest before registering its legos (`cirak.declare_kinds("layer", "metric", "turn")`; additive, idempotent), and a lego whose `kind` was never declared is a `RegistryError`. Facts follow the same rule: the table above is what çırak reads, and a catalog adds its own vocabulary with `cirak.declare_facts("grouped")` before registering (additive, idempotent, a çırak fact name is refused). A declared fact goes into `Facts.extra` untouched, `Facts.get("grouped")` reads it and `Facts.declared()` lists it for catalogs and listings; an undeclared name stays a `RegistryError`, so a misspelled fact is still caught. `Registry.kinds` lists what is declared, `Registry.facts(uri)` returns a `Facts` object for any URI (an empty one for unknown URIs), and `Facts.declared()` lists what a lego actually declared.

Where facts act:

- a flow step without `outputs` takes them from `returns`; without a `returns` fact the whole return value is written under the step's own name;
- a flow step's `bus` binds implicitly, minus any parameter already given in `params` or `inputs`;
- a gated step (`when`) whose outputs are all covered by `mutates` or `aliases` passes them through when skipped;
- a component without `partial` takes it from the fact; a `{uri, params}` value inside params whose lego has `kind: data` is not built but handed over as a `Deferred` (see the value language).

## The value language

The whole grammar hangs on one sentence: **`params` are construction arguments, `inputs` are call arguments.** `params` are handed over when an object is created; `inputs` are the values that flow into it when it is called.

Four markers connect the text world of YAML to the object world of Python:

**`$name$` substitutes values.** Resolution looks at the enclosing block's variables first, then at the global `params:` section; a name defined in both places is an error, so every `$name$` belongs to exactly one world. Substitution preserves types: when the entire value is `$passes$`, the raw value is inserted (an int stays an int, a list stays a list); inside a longer string it is stringified. `$name.field$` reads one field of a mapping value, one level deep. Escape a literal dollar sign as `$$`. There is no arithmetic inside placeholders; compute values in Python and substitute the result.

```yaml
params:
  n: 3
  name: model
  source: {uri: /io/demo/read_csv, tag: sales}

demo:
  uri: /a/b/c
  partial: true
  params:
    window: $n$
    label: run_$name$_v$n$
    tag: $source.tag$
```

`window` becomes the integer 3; `label` becomes the string `run_model_v3`; `tag` becomes `sales`. `uri`, `block` and `builder` fields must be literal, with one exception: inside a block they may be a block variable (`uri: $source.uri$`), which expansion fills before anything is resolved.

**`@name` injects built components.** `@smoother` is the constructed object registered under the component name `smoother`; `@tools.double` picks one member; `@tools` hands over the whole group as an ordered mapping of name to object, and an empty top level section (`plots: {}`) is an empty group that hands over `{}`. References may appear in component params and in flow step params, they form a build order, and cycles are rejected statically. Escape a literal at sign as `@@`.

**`{uri, params}` inside params is an inline component.** Any mapping with a string `uri` key inside a params tree is built the way a named component is (aliases resolved, registry existence and signature checked, `partial` from the key or from the fact) and the built object takes its place; it may nest. When the target lego declares `kind: data` the component is not built: a `Deferred(uri, params, target)` is passed instead, and whoever receives it calls `deferred.build(**extra)` once the data it needs exists.

```yaml
saver: {uri: /ckpt/proj/checkpoint, params: {policy: {uri: best, params: {monitor: val/rmse}}}}
loss:  {uri: /criterion/proj/cross_entropy, params: {weight: {uri: /data/proj/class_weights}}}
```

**`partial: true` defers the call.** Normally a component is built by calling its target with its params. With `partial: true` the call does not happen; the params are attached (`functools.partial`) and the callable itself becomes the component. Use it for graph nodes that must run later with flowing data, and for anything whose remaining arguments only exist at run time. A lego may declare `partial` as a fact so that recipes need not repeat it.

## Blocks

Blocks are parameterized templates. Variables are declared with `required` or `default`, in any number:

```yaml
blocks:
  smooth_chain:
    variables:
      window: {default: 3}
      passes: {required: true}
    spec:
      - uri: /series/demo/rolling_mean
        partial: true
        params: {window: $window$}
        repeat: $passes$
```

`spec` is the sequential form: a chain where each item feeds the next. `repeat` expands at the configuration level, producing that many separate nodes which are then built separately; there is no object sharing between copies. With `passes: 2, window: 5` the block compiles to:

```yaml
inputs: [s_in]
outputs: [s1]
graph:
  s0: {uri: /series/demo/rolling_mean, partial: true, params: {window: 5}, inputs: s_in}
  s1: {uri: /series/demo/rolling_mean, partial: true, params: {window: 5}, inputs: s0}
```

A spec block may name its two boundary wires with `inputs: [x]` and `outputs: [z]` (exactly one each); the chain then starts at `x` and its last node writes `z` instead of `s_in` and `s1`.

`graph` is the general form, for anything that is not a straight line:

```yaml
blocks:
  stats:
    inputs: [series]
    outputs: [summary]
    graph:
      mean: {uri: /stat/demo/mean, partial: true, inputs: series}
      spread: {uri: /stat/demo/spread, partial: true, inputs: series}
      summary: {uri: /stat/demo/combine, partial: true, inputs: [mean, spread]}
```

The rules:

1. The mapping key is both the node's name and its default output wire.
2. `inputs` is an ordered list and binds positionally: wire names say what flows, order says where each value lands in the call.
3. A node whose call returns several values declares `outputs: [q, r]` together with `unpack: true`; the returned tuple or list is spread by position, fewer values than outputs is an error, more are dropped with a warning. Without `unpack` the whole return value, a tuple included, is the node's single output. Declaring several outputs without `unpack` is a compile error; a `block:` or `model:` item takes no `unpack`.
4. Every input wire must be produced by another node or declared as a block input. A wire is written exactly once. Cycles are reported with their path. A node's keys are limited to `uri`, `block`, `model`, `params`, `partial`, `unpack`, `inputs`, `outputs`, `repeat` and `init`; anything else is an `unknown_key` error.
5. A node may use another block (`block:` instead of `uri:`). Nested blocks are flattened during compilation with dotted names (`h1.s0`), so builders always receive one flat graph. Recursive block references are an error. Inputs bind by count; when the node writes `outputs`, they bind **by name** to the block's declared outputs (`{block: head, outputs: [feature, logit]}` takes exactly those two wires, a name the block does not declare is an error), and a declared output nothing outside the block consumes draws a warning. Without `outputs` the block must have one output and the node's name is the wire.
6. `repeat` works in `graph` too: `h: {block: mlp_hidden, repeat: $depth$, inputs: [a0]}` chains copies `h_0`, `h_1`, ... where the first copy takes the node's inputs and the last writes the node's output; the node needs exactly one input and one output wire. `repeat: 0` removes the node and its output wire becomes an alias of its input wire.
7. `{model: name, inputs, outputs}` is a **reference node**: it is not built, the builder receives it as `GraphNode.ref` and takes the object from wherever it keeps its models at run time (a `models` input, typically). Two reference nodes with the same name mean the same object; several outputs wire by count.
8. A node's `init` key is passed through untouched to the builder in `GraphNode.extra`; çırak does not interpret it.

A block with `flow` instead of `spec` or `graph` is a flow block; see [Flow blocks and foreach](#flow-blocks-and-foreach). Exactly one of the three is allowed.

## Builders

A compiled graph is data. A builder is a registered callable that turns that data into one live object:

```python
@dataclass(frozen=True)
class GraphNode:
    name: str
    obj: object                 # the constructed object, None for a reference node
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    unpack: bool
    ref: str | None             # the referenced model's name, for {model: name} nodes
    extra: dict                 # keys passed through untouched, init among them

@dataclass(frozen=True)
class Graph:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    nodes: tuple[GraphNode, ...]
```

`nodes` arrive in topological order with every `obj` already constructed. The standard builder, `/builder/cirak/compose`, chains the graph into a plain callable and is about ten lines:

```python
def compose(graph):
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
```

`spread` (also in `cirak.std`, for other builders to reuse) is the positional unpack rule: a tuple or list, at least as many values as outputs, extra values dropped with a `CirakWarning`. `compose` refuses reference nodes; a builder that takes `models` at run time resolves them.

Which builder applies is stated on the component (`builder:`) or on the block definition; the component wins, and a builder that declares a kind must declare `builder`. The deep point of the contract: the meaning of a graph is fixed, its execution is the builder's choice. The same graph becomes a sequential function under compose and a parallel pipeline under the flow compiler; any other execution style is one registered builder away.

Builders are also flow steps. `{block: net, builder: /builder/proj/module, params: {seed: 7}, inputs: {models: models}, outputs: [net]}` in a flow compiles the block now, constructs its node objects now, and at run time calls `builder(graph, **params, **inputs)`; the return value is the step's output. `params` go to the builder, so the block itself is expanded with its variable defaults. This is how a model is built inside the pipeline from objects other steps produced.

## Flow

`flow` is the root pipeline. It compiles one to one onto tezgah's five node types, and tezgah supplies everything at run time: ordering from data edges, automatic parallelism of independent steps, an event stream, and run records.

```
node := step | builder step | block usage | foreach | pipeline | map | loop | branch

step:          {uri, params?, inputs?, outputs?, unpack?, when?, passthrough?, wait_for?, retries?, wait?}
builder step:  {block, builder, params?, inputs?, outputs?, unpack?, when?, passthrough?, wait_for?, retries?, wait?}
block usage:   {block, params?, inputs?, outputs?, wait_for?}
foreach:       {foreach: {over, item?, index?, count?, chain?, key?, node}}
pipeline:      {<name>: node, ..., inputs?, outputs?, wait_for?}
map:           {map: {body: node, over, item?, index?, collect?, parallel?, wait_for?}}
loop:          {loop: {body: node, carry, range, next?, index?, until?, trace?, outputs?, wait_for?}}
branch:        {branch: {decide, inputs?, cases: {<label>: node, ...}, default?, wait_for?}}
```

A mapping with `uri` is a step; with `block` and `builder` a builder step; with `block` alone a flow block usage; with `foreach`, `map`, `loop` or `branch` that construct; with none of these it is a pipeline whose keys are its child nodes (`inputs`, `outputs` and `wait_for` are reserved). The top level `flow:` is simply the root pipeline.

**Inputs of the root.** A flow reads three kinds of keys: what its own steps produce, what `run(inputs=...)` supplies (`device`, `record` and the like: root bus keys), and nothing else. The `params` section never reaches the bus; it only feeds `$name$` placeholders. A key nothing produces is a derived input of the root; `check` and `run` both compare it with the inputs you name, so a missing key is `missing_input` and a surplus one `unexpected_input`, in the static check already.

**Binding follows tezgah.** Steps bind by name: `inputs: [raw]` feeds the bus value `raw` into the parameter named `raw`; the mapping form takes a key, a list of keys (the parameter receives a list), a mapping of names to keys (it receives a dict) or a glob pattern (`"*_metrics"`, a dict of every matching key in the frame). This is the opposite of block graphs, which bind positionally, and both choices are deliberate: in a flow you wrote the functions and the static name check catches typos before the run, in a block graph you are calling foreign code whose parameter names you do not control. When `inputs` is omitted the function's own signature is the binding: every required parameter not given in `params` reads the bus key of its own name, so a function written against the bus vocabulary needs no wiring at all. Parameters with defaults stay unbound unless the lego's `bus` fact names them. Giving one parameter both in `params` and in `inputs` is `double_binding`.

**Outputs.** `outputs` may be a list, a mapping `{return_key: bus_key}` (which picks from a returned mapping and renames, `unpack` implied) or absent. Absent means the lego's `returns` fact, and without a fact the whole return value under the step's own name; side effect steps write `outputs: []` or declare `returns=None`. With a list, `unpack: true` picks each declared key from a returned mapping by name; a missing key fails the run, keys beyond the declared outputs are not written and tezgah warns about them.

```yaml
  split: {uri: /num/demo/divmod, inputs: [value], outputs: [q, r], unpack: true}
  whole: {uri: /num/demo/divmod, inputs: [value], outputs: [pair]}
  turn:  {uri: /turn/demo/train, outputs: {models: models_next, metrics: train_metrics}}
```

**Conditions.** `when` and `until` are either a bus key, whose truth value decides (`when: improved`, `until: stop`; a key never starts with `/`), or a predicate written as `{uri, params}`. `decide` on a branch is a predicate: a URI or `{uri, params}`. A predicate lego that declares a kind must declare `predicate`. Its required parameters are looked up in the frame by name; the ones çırak fixes through `params` are never looked up; its defaulted parameters bind only when the lego's `bus` fact names them (çırak hands the fact to tezgah as `when_bus`, `until_bus` or the branch's `bus`, minus what `params` fixed), and keep their default otherwise. A step with outputs and a `when` needs `passthrough`: çırak sets it when the lego's `mutates` or `aliases` facts cover every output, and you may write `passthrough: true` yourself; when skipped, every output is written from the same named input.

Branch labels are matched by dictionary lookup, so booleans work as labels: an unquoted `true:` in YAML is a real boolean and matches a decide that returns `True`. (A quoted `"true"` is a string and draws a lint warning.)

```yaml
flow:
  outputs: [next]
  seed: {uri: /num/demo/const, params: {value: $start$}, outputs: [n]}
  pick:
    branch:
      decide: /num/demo/is_even
      cases:
        true: {uri: /num/demo/halve, inputs: [n], outputs: [next]}
        false: {uri: /num/demo/triple, inputs: [n], outputs: [next]}
```

**Map and loop** follow tezgah's semantics exactly. Map collects in input order no matter what finishes first; `collect` is a list of body keys or a mapping of body key to parent key. Loop turns are sequential with carried state; `carry` is a list of keys or a mapping of carry name to parent key; `range` is an int, `[start, stop]`, `[start, stop, step]` or **a bus key** whose value decides the turns at run time (`range: epochs_left`); `next` names where the body writes each carry's next value, a suffix (`next: _next` reads `models` back from `models_next`) or a mapping; `index` names the wire that carries the current range value into the body; `until` is a body key or a predicate; `trace` accumulates one value per turn; `outputs` exports final carries. A body written as a pipeline without `inputs` and `outputs` is transparent: it reads what it needs from the turn frame and the enclosing frames, and exports what the loop consumes.

```yaml
  fan:
    map:
      over: xs
      item: x
      collect: {y: doubled}
      parallel: 4
      body: {uri: /num/demo/double, inputs: [x], outputs: [y]}

  epochs:
    loop:
      carry: [models, counters]
      next: _next
      range: epochs_left
      index: turn_index
      until: stop
      trace: {metrics: history}
      body:
        turn: {uri: /turn/demo/train, outputs: {models: models_next, counters: counters_next, metrics: metrics}}
        judge: {uri: /rule/demo/stop, inputs: [metrics]}
```

Before running, çırak also executes tezgah's own static validation on the compiled pipeline and surfaces its findings as regular problems with recipe locations attached; `check` does the same without building anything.

## Flow blocks and foreach

A block whose body is `flow` is a reusable piece of pipeline. Using it in a flow (`{block: name, params, inputs?, outputs?}`) fills its variables, expands it, and places the result as a pipeline named after the usage key. That pipeline is **transparent** on every side the usage does not declare: its steps read what they need from the enclosing frames and it exports what its siblings and containers consume (tezgah's rule). Writing `inputs: {outer: inner}` or `outputs: {inner: outer}` on the usage closes that side and renames at the boundary, so the same block can bind to different keys in different places.

```yaml
blocks:
  data:
    variables:
      source: {required: true}
      sets: {default: [train, valid, test]}
    flow:
      source: {uri: $source.uri$, params: $source.params$, outputs: [df]}
      frame:
        foreach: {over: $sets$, item: set, key: $set$,
                  node: {uri: /pre/demo/apply, params: {set: $set$}, inputs: {df: df}, outputs: [$set$_frame]}}

flow:
  outputs: [train_frame]
  data: {block: data, params: {source: {uri: /source/demo/parquet, params: {path: x.parquet}}}}
```

Variables are filled at expansion time: a value may be any YAML, a `@reference` string included, and `$name.field$` reaches one level into a mapping. `uri` and `block` fields inside a flow block may be block variables. Everything is resolved before tezgah sees it; the record's `flow.yaml` shows the opened blocks, never the templates.

**foreach** stamps one node template per element:

```yaml
      frame:
        foreach: {over: $sets$, item: set, key: $set$,
                  node: {uri: /pre/demo/apply, params: {set: $set$}, inputs: {df: df}, outputs: [$set$_frame]}}
      rule:
        foreach: {over: $rules$, index: i, count: n, chain: rules,
                  node: {uri: /rule/demo/rule, params: $item$}}
      rules_ruled: {uri: /std/demo/identity, inputs: {value: rules_$n$}}
```

The first stamps `frame_train`, `frame_valid`, `frame_test`; the second `rule_1` to `rule_n`, threaded through `rules_0` to `rules_n`.

- `over` is a list or a mapping (a variable or a literal); `item` names the element (default `item`; over a mapping, the entry's value), `index` the 1 based position, and `count` a variable holding the length that every node of the enclosing block or flow can read (`rules_$n$` above).
- `node` is one template: a step, a builder step or a block usage. Inside it `$item$`, `$item.field$` and `$i$` are substituted with their types preserved.
- `chain: K` threads a step template: node *i* gets `inputs: {K: K_<i-1>}` and `outputs: [K_<i>]` (the first reads `K_0`, the last writes `K_<count>`); the template may not write `inputs` or `outputs` itself.
- Node names are `<name>_<key>`: `key` is a template filled per element (`key: $set$`, `key: $m.name$`) and must give a string or an int. Without `key` the suffix is the mapping key over a mapping and the index over a list (`rule_1`, `rule_2`).
- An empty collection produces no nodes and a count of zero.
- Deliberately absent: nested foreach (write two blocks), conditions, filtering, branching on the element. A foreach copies structure; it never looks at values.

## Setup

`setup:` is an optional list of registered callables that `cirak run` invokes **before any
component is built**, in order:

```yaml
setup:
  - {uri: /util/proj/seed_everything, params: {seed: 42}}
```

It exists for process level preparation that must precede construction: seeding random number
generators is the canonical case, since component factories may draw random state the moment they
are built (a neural network layer initializing its weights, for example). Placing the seeding in
`flow:` alone is too late for those draws; placing it in `setup:` as well as in the flow pins both
construction time and run time behavior.

Setup entries take only `uri` and `params`, and the params are plain values: `$param$`
substitution applies as everywhere, but `@component` references and inline components are rejected at validation,
because components do not exist yet when setup runs. The signature check applies to setup entries
like any component. `cirak check` validates the section without running it.

## Layers and merging

A recipe is a tree of layers. The files given on the command line form the top layer; every file an `include` names is a layer of its own placed **below** the file that includes it, recursively, in list order from bottom to top (the first entry is the lowest). `--set` assignments form one more layer on top of everything. A file reached by two paths is loaded once, at its first position. Includes are paths relative to the including file or fragment URIs from the registry; cycles are errors.

Two merge rules, one per direction:

1. **Inside a layer, strict.** The files of the command line are merged with no override: the same leaf defined in two of them is a conflict, even with equal values, and the error names both locations. Mappings merge recursively; leaves are scalars and lists.
2. **Between layers, the upper one wins leaf by leaf.** Mappings merge recursively, lists and scalars are replaced whole (a mapping meeting a scalar is replaced as a subtree), `plugins` are pooled. A lego record, a mapping with a `uri` or `block` key, is one call: written above it replaces the whole value below (`checkpoint: {uri: last}` above `checkpoint: {uri: best, params: {monitor: val/rmse}}` yields `{uri: last}`), while a partial mapping without `uri` or `block` merges into the existing call (`--set data.split.params.val=null` changes one param). Inside a layer a record written twice is a conflict. Every override is recorded: `resolved.yaml` marks the winning value with a comment naming what it replaced (`n: 3  # --set overrides base.yaml:2, top.yaml:3`), and `cirak check --layers` prints the tree with the leaves each file overrode.

```
layers, bottom to top:
  1. base.yaml (included by exp.yaml)
  2. exp.yaml
       overrides training.epochs (base.yaml:11)
  3. --set
       overrides params.lr (exp.yaml:4)
```

The working style: a base file, thin experiment files that include it and override a few leaves, and `--set` for the run at hand. Alternatives that must not be combined stay separate files on the command line, where combining them fails loudly instead of silently picking a winner.

## Validation

The philosophy is inherited from tezgah: a long run should never die at minute forty because of something knowable at second two. All findings are collected across the whole recipe and reported together:

```
ConfigError: 3 problems found:
  1. [merge_conflict] alias.apply is defined twice: base.yaml:4 and exp.yaml:2
  2. [unknown_uri] /stat/proj/meen is not registered (stats.yaml:12); did you mean /stat/proj/mean?
  3. [missing_variable] block 'smooth_chain' requires variable 'passes' (main.yaml:7)
```

The rulebook covers YAML parsing and duplicate keys, include resolution and layering, merge conflicts, alias chains and cycles, placeholder scoping and shadowing, reference existence and cycles, block arity and graph integrity, builder presence and kind, registry existence with suggestions, signature checking against the target's actual parameters (components, inline components, graph nodes, flow steps and builder steps), fact driven kind checks, structural checks on every flow construct, foreach limits, and, once the recipe is clean, tezgah's own resolution of the compiled pipeline with the run inputs you name. Findings that do not block compilation (an unused component, a never consumed output, a quoted boolean label, an unused block variable) are reported as warnings. Expanded nodes report the line of the template they came from.

## Error model

| type | when | payload |
|---|---|---|
| `RegistryError` | at registration time | message |
| `ConfigError` | at the compile gate | `.problems`, the complete list |
| `BuildError` | while constructing objects | component name, original exception via `__cause__` |

Error variety lives as data, not as class hierarchy: each problem carries a machine readable `kind`, and there is deliberately no exception class per rule. Run time failures are not çırak's: tezgah's `RunError` and `ContractError` pass through untouched, with their full payloads.

## Command line

```
cirak check base.yaml [--set path=value ...] [--input NAME ...] [--layers]
cirak show main.yaml [--set ...] [--expanded | --flow [--input NAME ...]]
cirak run main.yaml [--set ...] [--input NAME=VALUE ...] [--record runs/x1] [--executor thread] [--workers 8]
cirak ls /series [--recipe main.yaml] [--kind layer]
cirak search rolling [--recipe main.yaml]
```

| command | does |
|---|---|
| `check` | Compiles, never runs; prints every problem; `--layers` prints the layer tree first |
| `show` | Prints the resolved recipe; `--expanded` adds the expanded block graphs; `--flow` prints the expanded flow annotated with what tezgah resolved |
| `run` | Compiles and runs on tezgah; records land under `--record` |
| `ls` | Lists registry entries under a path with their kind and facts |
| `search` | Searches URIs and descriptions, ignoring case |

`--set path=value` overrides one leaf as the top layer; the path is dotted (`params.n`, `training.stop`) and the value is read as YAML (`5`, `null`, `[]`, `{a: 1}`, `runs/x`). `--input` names the root bus keys the run supplies (`check` and `show --flow` take the names, `run` takes `NAME=VALUE` with a YAML value). Exit codes: 0 clean (warnings allowed), 1 problems or a failed run, 2 usage error. `ls` and `search` see installed catalogs by themselves; add `--recipe` to also load a recipe's `plugins:` registrations. Output is colored only on a real terminal, honors `NO_COLOR`, and `show` is never colored because its output is data.

## Records and reproducibility

`cirak run main.yaml --record runs/x1` produces:

```
runs/x1/
  resolved.yaml   the recipe with aliases and placeholders resolved, overrides marked with comments
  flow.yaml       the dump document: components, blocks and the expanded flow
  events.jsonl    tezgah's event stream, one JSON object per line
  run.json        run id, status, timing, full status tree
  stdout.txt
  stderr.txt
```

`resolved.yaml` keeps block definitions as templates (expansion is deterministic), which is exactly what keeps it a valid recipe: `cirak run runs/x1/resolved.yaml --input device=cpu` reproduces the run from a single file. `flow.yaml` has three sections: `components` (every non reserved section of the resolved recipe), `blocks` (the spec and graph blocks, still templates) and `flow` (blocks opened, foreach unrolled, every step's outputs written out, with comments for implicit bindings, pattern matches, derived reads and exports). It is the graph that ran, readable without the registry's facts; `cirak show --flow` prints the same document without running.

## Python API

```python
register(uri, target=None, *, description=None, **facts)
register_many(prefix, entries)
register_fragment(uri, path, *, description)
register_std(registry)
check(paths, *, sets=None, inputs=None) -> list[Problem]
resolve(paths, *, sets=None) -> dict
layers(paths, *, sets=None) -> str
flow_dump(paths, *, sets=None, inputs=None) -> str
run(paths, *, sets=None, inputs=None, sinks=None, record_dir=None, executor="serial", workers=None) -> tezgah.Report
```

`register` is the single entry point of the registry: a call or a decorator, with the lego's facts as keyword
arguments. `sets` is a list of `(dotted_path, value)` pairs, the top layer. `inputs` are the root bus keys: names for `check` and `flow_dump`, a mapping of name to value for `run`. `sinks` are callables subscribed to tezgah's event stream. `check` returns findings without raising. `resolve` returns the resolved recipe and raises `ConfigError` on errors. `run` compiles, builds, and executes on tezgah; `executor` accepts tezgah's `"serial"`, `"thread"` and `"dask"`. The command line is a thin shell over these functions.

## Development

```
uv sync
uv run pytest
```

The test suite is the executable contract: every validation rule asserts its `Problem.kind`, the flow tests run real pipelines on tezgah across its node types, and `tests/test_kalfa_templates.py` opens a full template with a mock registry, compares the dump document with `tests/fixtures/*.flow.yaml` (synced from kalfa by `tests/fixtures/regenerate_dumps.py`, which runs the same comparison) and runs it.
