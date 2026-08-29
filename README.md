# çırak

**A YAML front end for Python pipelines: describe your computation as recipes assembled from registered building blocks, catch every configuration error before anything runs, then execute on [tezgah](https://github.com/dboncukcu/tezgah).**

çırak is a compiler, not a runtime. It reads declarative YAML recipes, resolves every referenced building block through a registry, validates the whole configuration in one pass, constructs the Python objects, and hands the assembled pipeline to tezgah for execution. The division of labor is strict: çırak never executes anything, tezgah never parses anything.

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
- [The value language](#the-value-language)
- [Blocks](#blocks)
- [Builders](#builders)
- [Flow](#flow)
- [Strict merging and variants](#strict-merging-and-variants)
- [Validation](#validation)
- [Error model](#error-model)
- [Command line](#command-line)
- [Records and reproducibility](#records-and-reproducibility)
- [Python API](#python-api)
- [Development](#development)

## Why

Configuration layers around pipeline code tend to rot the same way in every project: a hand rolled YAML loader, values stitched together through ad hoc overrides, a typo that surfaces forty minutes into a long run, and results that nobody can reproduce a month later. çırak makes five bets against that:

1. **Everything is a building block.** Functions, classes, builders, even packaged YAML fragments are registered under URIs and assembled declaratively. Swapping an implementation means swapping a URI, not editing code.
2. **One grammar.** There are six reserved keys; every other key follows a single component grammar. File boundaries carry no meaning: a recipe may live in one file or twenty, and any section may appear in any file.
3. **Selection, not overriding.** Merging is strict. The same key defined twice is an error, even when the values are equal, so merge order cannot matter. Experiment variants are built by choosing which files to combine, never by overriding.
4. **All problems at once, before anything runs.** Unknown URIs (with suggestions), missing variables, signature mismatches, graph cycles, dangling references: collected across the whole recipe and reported together, each with file and line.
5. **A run's record reproduces it.** Every recorded run contains a `resolved.yaml` that is itself a valid recipe. One file is enough to run the same thing again.

## Install

Requires Python 3.13 or newer. The dependencies are tezgah (which itself has none) and ruamel.yaml.

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
  average: {uri: /stat/demo/mean, inputs: {values: smoothed}, outputs: [average]}
```

Point `input` at any small CSV with a numeric `amount` column, then check it, run it, keep the record:

```
cirak check report.yaml
cirak run report.yaml --record runs/first
```

```
run r_7f3a2c9b: ok
outputs:
  average: 17.25
```

`runs/first/` now holds tezgah's event stream and run summary, plus `resolved.yaml`: the recipe with every alias and placeholder resolved. It is a valid recipe in its own right:

```
cirak run runs/first/resolved.yaml
```

## Core concepts

| term | meaning |
|---|---|
| recipe | The configuration: one or more YAML files combined by strict merging |
| registry | The catalog of building blocks, addressed by URI |
| component | A named, buildable definition: `{uri or block, params, ...}` |
| group | A mapping that contains components; referenced as a whole or by member |
| block | A parameterized template: `variables` plus `spec` or `graph` |
| graph | Named nodes connected by named wires, single assignment, acyclic |
| builder | A registered callable that turns a compiled graph into one object |
| flow | The root pipeline, compiled to tezgah and executed there |
| fragment | A registered YAML piece that recipes can include by URI |
| problem | One finding: severity, kind, message, file, line, hint |

A recipe has exactly six reserved top level keys: `include`, `plugins`, `alias`, `params`, `blocks`, `flow`. Every other top level key is a component or a group of components. çırak recognizes no section names beyond the six; `metrics:` or `tools:` are just names you chose.

## The registry

URIs follow one scheme:

```
/kind/provider/name[/subname...]
```

At least three lowercase segments. The first names the kind (`table`, `series`, `builder`, `flow`), the second the provider, the rest is a free hierarchy. çırak attaches no semantics to any kind; the scheme exists for browsing and search.

Registration comes in four forms:

```python
cirak.register("/table/demo/dropna", dropna, description="Drop rows with missing values")

@cirak.register("/report/proj/weekly", description="Render the weekly summary")
def weekly(rows, title): ...

cirak.register_many("/series/statlib", {
    "rolling_mean": ("statlib.series:rolling_mean", "Rolling mean over a window"),
    "diff": "statlib.series:diff",
})

cirak.register_fragment("/flow/proj/report", path, description="CSV to JSON summary flow")
```

Rules worth knowing:

1. Descriptions are optional but never absent. When you write one it wins; when you do not, çırak derives one: the name and signature for callables (`read_csv(path, headers: bool = True)`), the import string for lazy targets, the file path for fragments. Either way `ls` and `search` always have something real to show. Curated catalogs should still write their own.
2. String targets in the form `"module.path:name"` load lazily: registering a catalog imports nothing, the module is imported the first time the URI is actually built. Heavy libraries stay unloaded until a recipe uses them.
3. Registering the same URI again with an identical target is silently ignored (double imports are harmless). A different target raises `RegistryError`.
4. Discovery is twofold. Modules listed in a recipe's `plugins:` section are imported at compile time, and the directories of the recipe files are placed on `sys.path` first, so a `parts.py` sitting next to the recipe is found without installing anything. Installed packages announce their catalogs through the `cirak.plugins` entry point group and are loaded when the command line starts.
5. çırak ships a small standard catalog, registered the moment you `import cirak`. Its only member today is `/builder/cirak/compose` (see [Builders](#builders)). The bar for entry is strict: domain free, zero dependencies, small.

Browse with `cirak ls /series` and `cirak search rolling`.

## The value language

The whole grammar hangs on one sentence: **`params` are construction arguments, `inputs` are call arguments.** `params` are handed over when an object is created; `inputs` are the values that flow into it when it is called.

Three markers connect the text world of YAML to the object world of Python:

**`$name$` substitutes values.** Resolution looks at the enclosing block's variables first, then at the global `params:` section; a name defined in both places is an error, so every `$name$` belongs to exactly one world. Substitution preserves types: when the entire value is `$passes$`, the raw value is inserted (an int stays an int, a list stays a list); inside a longer string it is stringified. Escape a literal dollar sign as `$$`. There is no arithmetic inside placeholders; compute values in Python and substitute the result.

```yaml
params:
  n: 3
  name: model

demo:
  uri: /a/b/c
  partial: true
  params:
    window: $n$
    label: run_$name$_v$n$
```

`window` becomes the integer 3; `label` becomes the string `run_model_v3`.

**`@name` injects built components.** `@smoother` is the constructed object registered under the component name `smoother`; `@tools.double` picks one member; `@tools` hands over the whole group as an ordered mapping of name to object. References may appear in component params and in flow step params, they form a build order, and cycles are rejected statically. Escape a literal at sign as `@@`.

**`partial: true` defers the call.** Normally a component is built by calling its target with its params. With `partial: true` the call does not happen; the params are attached (`functools.partial`) and the callable itself becomes the component. Use it for graph nodes that must run later with flowing data, and for anything whose remaining arguments only exist at run time.

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
3. A node with several results declares `outputs: [q, r]`; the returned tuple is mapped by position.
4. Every input wire must be produced by another node or declared as a block input. A wire is written exactly once. Cycles are reported with their path.
5. A node may use another block (`block:` instead of `uri:`). Nested blocks are flattened during compilation with dotted names (`h1.s0`), so builders always receive one flat graph. Recursive block references are an error.

## Builders

A compiled graph is data. A builder is a registered callable that turns that data into one live object:

```python
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
```

`nodes` arrive in topological order with every `obj` already constructed. The standard builder, `/builder/cirak/compose`, chains the graph into a plain callable and is about ten lines:

```python
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
```

Which builder applies is stated on the component (`builder:`) or on the block definition; the component wins. The deep point of the contract: the meaning of a graph is fixed, its execution is the builder's choice. The same graph becomes a sequential function under compose and a parallel pipeline under the flow compiler; any other execution style is one registered builder away.

## Flow

`flow` is the root pipeline. It compiles one to one onto tezgah's five node types, and tezgah supplies everything at run time: ordering from data edges, automatic parallelism of independent steps, an event stream, and run records.

```
node := step | pipeline | map | loop | branch

step:      {uri, params?, inputs?, outputs?, when?, wait_for?, retries?, wait?}
pipeline:  {<name>: node, ..., inputs?, outputs?, wait_for?}
map:       {map: {body: node, over, item?, index?, collect?, parallel?, wait_for?}}
loop:      {loop: {body: node, carry, max_iter, until?, trace?, outputs?, wait_for?}}
branch:    {branch: {decide, inputs?, cases: {<label>: node, ...}, default?, wait_for?}}
```

A mapping with `uri` is a step; with `map`, `loop` or `branch` it is that container; with none of these it is a pipeline whose keys are its child nodes (`inputs`, `outputs` and `wait_for` are reserved). The top level `flow:` is simply the root pipeline. It takes no external inputs: everything a flow consumes is either bound from `params` or produced by its own steps.

Steps bind by name, following tezgah: `inputs: [raw]` feeds the bus value `raw` into the parameter named `raw`, and `inputs: {table: raw}` renames on the way in. This is the opposite of block graphs, which bind positionally, and both choices are deliberate: in a flow you wrote the functions and the static name check catches typos before the run, in a block graph you are calling foreign code whose parameter names you do not control. When `inputs` or `outputs` is omitted on a step it is treated as empty.

Conditions (`when`, `until`, `decide`) are URIs of registered predicates; `{uri: ..., params: {...}}` attaches configuration to them. Branch labels are matched by dictionary lookup, so booleans work as labels: an unquoted `true:` in YAML is a real boolean and matches a decide that returns `True`. (A quoted `"true"` is a string and draws a lint warning.)

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

Map and loop follow tezgah's semantics exactly: map collects in input order no matter what finishes first, loop turns are sequential with carried state and a mandatory `max_iter`:

```yaml
  fan:
    map:
      over: xs
      item: x
      collect: doubled
      parallel: 4
      body: {uri: /num/demo/double, inputs: [x], outputs: [y]}

  refine:
    loop:
      carry: {value: seed}
      until: /num/demo/converged
      max_iter: 100
      trace: {error: error_history}
      outputs: {value: best}
      body:
        inputs: [value]
        outputs: {value_next: value}
        improve: {uri: /num/demo/improve, inputs: [value], outputs: [value_next, error]}
```

Before running, çırak also executes tezgah's own static validation on the compiled pipeline and surfaces its findings as regular problems with recipe locations attached.

## Strict merging and variants

A recipe is the strict merge of all its files: the ones given on the command line plus everything reached through `include:` (paths relative to the including file, or fragment URIs from the registry; the same file is merged once even when reached twice; cycles are errors).

1. Mappings merge recursively.
2. Leaves are scalars and lists. The same leaf defined in two files is a conflict, even with equal values, and the error names both locations. (`include` and `plugins` are the one exception: they are pooled across files.)
3. There is no list concatenation and no override marker. Merging is commutative; file order never matters.

The payoff is a working style: variants are selected, not patched.

```
cirak run base.yaml smooth_fast.yaml
cirak run base.yaml smooth_precise.yaml
```

Both variant files may define the same alias names pointing at different URIs; the rest of the recipe stays untouched. Combining both variants at once fails loudly instead of silently picking a winner.

## Validation

The philosophy is inherited from tezgah: a long run should never die at minute forty because of something knowable at second two. All findings are collected across the whole recipe and reported together:

```
ConfigError: 3 problems found:
  1. [merge_conflict] alias.apply is defined twice: base.yaml:4 and exp.yaml:2
  2. [unknown_uri] /stat/proj/meen is not registered (stats.yaml:12); did you mean /stat/proj/mean?
  3. [missing_variable] block 'smooth_chain' requires variable 'passes' (main.yaml:7)
```

The rulebook covers thirty six numbered rules: YAML parsing and duplicate keys, include resolution, merge conflicts, alias chains and cycles, placeholder scoping and shadowing, reference existence and cycles, block arity and graph integrity, builder presence, registry existence with suggestions, signature checking against the target's actual parameters, and structural checks on flow nodes. Findings that do not block compilation (an unused component, a never consumed output, a quoted boolean label) are reported as warnings.

## Error model

| type | when | payload |
|---|---|---|
| `RegistryError` | at registration time | message |
| `ConfigError` | at the compile gate | `.problems`, the complete list |
| `BuildError` | while constructing objects | component name, original exception via `__cause__` |

Error variety lives as data, not as class hierarchy: each problem carries a machine readable `kind`, and there is deliberately no exception class per rule. Run time failures are not çırak's: tezgah's `RunError` and `ContractError` pass through untouched, with their full payloads.

## Command line

```
cirak check base.yaml exp.yaml
cirak show main.yaml [--expanded]
cirak run main.yaml [--record runs/x1] [--executor thread] [--workers 8]
cirak ls /series [--recipe main.yaml]
cirak search rolling [--recipe main.yaml]
```

| command | does |
|---|---|
| `check` | Compiles, never runs; prints every problem |
| `show` | Prints the resolved recipe; `--expanded` adds the expanded block graphs |
| `run` | Compiles and runs on tezgah; records land under `--record` |
| `ls` | Lists registry entries under a path |
| `search` | Searches URIs and descriptions, ignoring case |

Exit codes: 0 clean (warnings allowed), 1 problems or a failed run, 2 usage error. `ls` and `search` see installed catalogs by themselves; add `--recipe` to also load a recipe's `plugins:` registrations. Output is colored only on a real terminal, honors `NO_COLOR`, and `show` is never colored because its output is data.

## Records and reproducibility

`cirak run main.yaml --record runs/x1` produces:

```
runs/x1/
  resolved.yaml   the recipe with aliases and placeholders resolved
  events.jsonl    tezgah's event stream, one JSON object per line
  run.json        run id, status, timing, full status tree
  stdout.txt
  stderr.txt
```

`resolved.yaml` keeps block definitions as templates (expansion is deterministic), which is exactly what keeps it a valid recipe: `cirak run runs/x1/resolved.yaml` reproduces the run from a single file. The expanded graphs are a debugging view, available through `cirak show --expanded`.

## Python API

```python
register(uri, target=None, *, description)
register_many(prefix, entries)
register_fragment(uri, path, *, description)
register_std(registry)
check(paths) -> list[Problem]
resolve(paths) -> dict
run(paths, *, record_dir=None, executor="seri", workers=None) -> tezgah.Report
```

`check` returns findings without raising. `resolve` returns the resolved recipe and raises `ConfigError` on errors. `run` compiles, builds, and executes on tezgah; `executor` accepts tezgah's `"seri"`, `"thread"` and `"dask"`. The command line is a thin shell over these functions.

## Development

```
uv sync
uv run pytest
```

The test suite is the executable contract: every validation rule asserts its `Problem.kind`, and the flow tests run real pipelines on tezgah across its node types.
