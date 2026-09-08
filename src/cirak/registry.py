import inspect
import re
from dataclasses import dataclass, field
from importlib import import_module

from .errors import RegistryError

URI_PATTERN = re.compile(r"^(/[a-z0-9_]+){3,}$")

OWN_KINDS = ("builder", "predicate", "data")

FACT_NAMES = ("kind", "alias", "returns", "bus", "mutates", "aliases", "partial", "state", "refs")


class _Unset:
    def __repr__(self):
        return "UNSET"


UNSET = _Unset()


@dataclass(frozen=True)
class Facts:
    """What a lego declares about itself at registration time.

    ``returns`` is UNSET when the lego says nothing about its output, ``None``
    when it declares itself a side effect.
    """

    kind: str | None = None
    alias: tuple[str, ...] = ()
    returns: object = UNSET
    bus: dict = field(default_factory=dict)
    mutates: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    partial: bool = False
    state: object = False
    refs: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def get(self, name, default=None):
        """A fact by name: one of çırak's own, or one the catalog declared with ``declare_facts``."""
        if name in FACT_NAMES:
            return getattr(self, name)
        return self.extra.get(name, default)

    def declared(self) -> dict:
        """The facts that differ from the empty declaration, çırak's own and the catalog's, for listings."""
        found = {}
        for name in FACT_NAMES:
            value = getattr(self, name)
            if name == "returns":
                if value is not UNSET:
                    found[name] = value
            elif value not in (None, (), {}, False):
                found[name] = list(value) if isinstance(value, tuple) else value
        for name, value in self.extra.items():
            if value not in (None, (), {}, False):
                found[name] = list(value) if isinstance(value, tuple) else value
        return found


@dataclass(frozen=True)
class Entry:
    uri: str
    target: object
    description: str
    fragment: bool = False
    facts: Facts = field(default_factory=Facts)


def _describe(target, description) -> str:
    if isinstance(description, str) and description.strip():
        return description
    if isinstance(target, str):
        return target
    name = getattr(target, "__name__", None) or type(target).__name__
    try:
        return f"{name}{inspect.signature(target)}"
    except (TypeError, ValueError):
        return f"{name}(...)"


def _names(value, uri, fact):
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise RegistryError(f"{uri}: {fact} must be a string or a list of strings, got {value!r}")


def _normalize_facts(uri, facts, declared=()) -> Facts:
    unknown = [name for name in facts if name not in FACT_NAMES and name not in declared]
    if unknown:
        raise RegistryError(f"{uri}: unknown facts {unknown}; çırak's facts are {list(FACT_NAMES)}, the catalog "
                            f"declared {list(declared)}, add yours with declare_facts()")
    kind = facts.get("kind")
    if kind is not None and not isinstance(kind, str):
        raise RegistryError(f"{uri}: kind must be a string")
    alias = _names(facts["alias"], uri, "alias") if "alias" in facts else ()
    for name in alias:
        if not name or "/" in name:
            raise RegistryError(f"{uri}: alias {name!r} must be a short name without '/'")
    returns = facts.get("returns", UNSET)
    if returns is not UNSET and returns is not None and not isinstance(returns, str):
        returns = list(_names(returns, uri, "returns"))
    bus = facts.get("bus") or {}
    if isinstance(bus, dict):
        for param, key in bus.items():
            if not isinstance(param, str) or not isinstance(key, str):
                raise RegistryError(f"{uri}: bus must map parameter names to bus keys or patterns")
        bus = dict(bus)
    else:
        bus = {name: name for name in _names(bus, uri, "bus")}
    mutates = _names(facts["mutates"], uri, "mutates") if facts.get("mutates") else ()
    aliases = _names(facts["aliases"], uri, "aliases") if facts.get("aliases") else ()
    partial = facts.get("partial", False)
    if not isinstance(partial, bool):
        raise RegistryError(f"{uri}: partial must be a boolean")
    state = facts.get("state", False)
    if not isinstance(state, bool):
        state = _names(state, uri, "state")
        if not isinstance(returns, list):
            raise RegistryError(f"{uri}: state names return keys, so returns must be a list")
        for name in state:
            if name not in returns:
                raise RegistryError(f"{uri}: state names {name!r} but returns does not include it")
    if isinstance(returns, list):
        for name in mutates:
            if name not in returns:
                raise RegistryError(f"{uri}: mutates names {name!r} but returns does not include it")
    refs = facts.get("refs") or {}
    if not isinstance(refs, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                             for key, value in refs.items()):
        raise RegistryError(f"{uri}: refs must map parameter names to type names")
    extra = {name: value for name, value in facts.items() if name in declared}
    return Facts(kind, alias, returns, bus, mutates, aliases, partial, state, dict(refs), extra)


def _check_against_signature(uri, target, facts: Facts) -> None:
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return
    parameters = list(signature.parameters.values())
    takes_kwargs = any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters)
    named = {parameter.name: parameter for parameter in parameters
             if parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)}
    for param in facts.bus:
        parameter = named.get(param)
        if parameter is None or parameter.default is parameter.empty:
            raise RegistryError(f"{uri}: bus lists {param!r} but only parameters with a default "
                                f"bind implicitly")
    for fact, names in (("mutates", facts.mutates), ("aliases", facts.aliases),
                        ("refs", tuple(facts.refs))):
        for name in names:
            if name not in named and not takes_kwargs:
                raise RegistryError(f"{uri}: {fact} names {name!r} but the signature has no such "
                                    f"parameter")


class Registry:
    """The catalog of legos, addressed by URI, with the kinds a catalog declares.

    çırak owns three kinds (``builder``, ``predicate`` and ``data``); a catalog
    declares its own with ``declare_kinds`` and every lego's ``kind`` must be a
    declared one. Facts work the same way: çırak owns ``FACT_NAMES`` and reads
    them, a catalog declares its own with ``declare_facts`` and çırak stores them
    for listings without interpreting them.
    """

    def __init__(self):
        self._entries: dict[str, Entry] = {}
        self._resolved: dict[str, object] = {}
        self._checked: set[str] = set()
        self._kinds: list[str] = list(OWN_KINDS)
        self._facts: list[str] = []

    def declare_kinds(self, *names) -> None:
        for name in names:
            if not isinstance(name, str) or not name:
                raise RegistryError(f"kind names must be strings, got {name!r}")
            if name not in self._kinds:
                self._kinds.append(name)

    def declare_facts(self, *names) -> None:
        """Let the catalog register legos with facts of its own; çırak stores them, it reads none of them."""
        for name in names:
            if not isinstance(name, str) or not name:
                raise RegistryError(f"fact names must be strings, got {name!r}")
            if name in FACT_NAMES:
                raise RegistryError(f"{name!r} is one of çırak's own facts, it needs no declaration")
            if name not in self._facts:
                self._facts.append(name)

    @property
    def kinds(self) -> list[str]:
        return list(self._kinds)

    @property
    def declared_facts(self) -> list[str]:
        return list(self._facts)

    def register(self, uri, target=None, *, description=None, **facts):
        """Register ``target`` under ``uri`` with the facts it declares, or return a decorator."""
        if target is None:
            def decorator(fn):
                self._add(Entry(uri, fn, _describe(fn, description),
                                facts=_normalize_facts(uri, facts, self._facts)))
                return fn
            return decorator
        self._add(Entry(uri, target, _describe(target, description),
                        facts=_normalize_facts(uri, facts, self._facts)))
        return target

    def register_many(self, prefix, entries) -> None:
        for name, value in entries.items():
            if isinstance(value, tuple):
                if len(value) != 2:
                    raise RegistryError(f"register_many entry {name!r} must be a target "
                                        f"or a (target, description) pair")
                target, description = value
            else:
                target, description = value, None
            self.register(f"{prefix}/{name}", target, description=description)

    def register_fragment(self, uri, path, *, description=None) -> None:
        self._add(Entry(uri, str(path), _describe(str(path), description), fragment=True))

    def lookup(self, uri) -> Entry | None:
        return self._entries.get(uri)

    def facts(self, uri) -> Facts:
        entry = self._entries.get(uri)
        return entry.facts if entry is not None else Facts()

    def uris(self) -> list[str]:
        return list(self._entries)

    def fragments(self) -> dict[str, str]:
        return {entry.uri: entry.target for entry in self._entries.values() if entry.fragment}

    def aliases(self) -> dict[str, str]:
        table = {}
        for uri, entry in self._entries.items():
            for name in entry.facts.alias:
                table[name] = uri
        return table

    def ls(self, prefix: str, kind=None) -> list[Entry]:
        cleaned = prefix.rstrip("/")
        return [entry for uri, entry in sorted(self._entries.items())
                if (not cleaned or uri == cleaned or uri.startswith(cleaned + "/"))
                and (kind is None or entry.facts.kind == kind)]

    def search(self, term: str) -> list[Entry]:
        needle = term.lower()
        return [entry for uri, entry in sorted(self._entries.items())
                if needle in uri.lower() or needle in entry.description.lower()]

    def resolve(self, uri):
        if uri in self._resolved:
            return self._resolved[uri]
        entry = self._entries.get(uri)
        if entry is None:
            raise RegistryError(f"{uri} is not registered")
        if entry.fragment:
            raise RegistryError(f"{uri} is a fragment, not a callable")
        target = entry.target
        if isinstance(target, str):
            module_path, _, attribute = target.partition(":")
            target = getattr(import_module(module_path), attribute)
        if uri not in self._checked:
            _check_against_signature(uri, target, entry.facts)
            self._checked.add(uri)
        self._resolved[uri] = target
        return target

    def resolve_quietly(self, uri):
        try:
            return self.resolve(uri)
        except Exception:
            return None

    def _add(self, entry: Entry) -> None:
        if not URI_PATTERN.fullmatch(entry.uri):
            raise RegistryError(f"invalid uri {entry.uri!r}: expected /kind/provider/name "
                                f"with lowercase segments")
        if isinstance(entry.target, str) and not entry.fragment and ":" not in entry.target:
            raise RegistryError(f"{entry.uri} has an invalid import string {entry.target!r}; "
                                f"expected 'module.path:name'")
        existing = self._entries.get(entry.uri)
        if existing is not None:
            same_target = existing.target is entry.target or existing.target == entry.target
            if same_target and existing.description == entry.description \
                    and existing.fragment == entry.fragment and existing.facts == entry.facts:
                return
            raise RegistryError(f"{entry.uri} is already registered with a different target")
        kind = entry.facts.kind
        if kind is not None and kind not in self._kinds:
            raise RegistryError(f"{entry.uri}: kind {kind!r} is not declared; declared kinds are "
                                f"{self._kinds}, add yours with declare_kinds()")
        taken = self.aliases()
        for name in entry.facts.alias:
            if name in taken and taken[name] != entry.uri:
                raise RegistryError(f"{entry.uri}: alias {name!r} is already taken by {taken[name]}")
        if callable(entry.target):
            _check_against_signature(entry.uri, entry.target, entry.facts)
            self._checked.add(entry.uri)
        self._entries[entry.uri] = entry


registry = Registry()


def register(uri, target=None, *, description=None, **facts):
    return registry.register(uri, target, description=description, **facts)


def declare_kinds(*names) -> None:
    registry.declare_kinds(*names)


def declare_facts(*names) -> None:
    registry.declare_facts(*names)


def register_many(prefix, entries) -> None:
    registry.register_many(prefix, entries)


def register_fragment(uri, path, *, description=None) -> None:
    registry.register_fragment(uri, path, description=description)
