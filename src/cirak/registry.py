import inspect
import re
from dataclasses import dataclass
from importlib import import_module

from .errors import RegistryError

URI_PATTERN = re.compile(r"^(/[a-z0-9_]+){3,}$")


@dataclass(frozen=True)
class Entry:
    uri: str
    target: object
    description: str
    fragment: bool = False


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


class Registry:
    def __init__(self):
        self._entries: dict[str, Entry] = {}
        self._resolved: dict[str, object] = {}

    def register(self, uri, target=None, *, description=None):
        if target is None:
            def decorator(fn):
                self._add(Entry(uri, fn, _describe(fn, description)))
                return fn
            return decorator
        self._add(Entry(uri, target, _describe(target, description)))
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

    def uris(self) -> list[str]:
        return list(self._entries)

    def fragments(self) -> dict[str, str]:
        return {entry.uri: entry.target for entry in self._entries.values() if entry.fragment}

    def ls(self, prefix: str) -> list[Entry]:
        cleaned = prefix.rstrip("/")
        return [entry for uri, entry in sorted(self._entries.items())
                if not cleaned or uri == cleaned or uri.startswith(cleaned + "/")]

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
                    and existing.fragment == entry.fragment:
                return
            raise RegistryError(f"{entry.uri} is already registered with a different target")
        self._entries[entry.uri] = entry


registry = Registry()


def register(uri, target=None, *, description=None):
    return registry.register(uri, target, description=description)


def register_many(prefix, entries) -> None:
    registry.register_many(prefix, entries)


def register_fragment(uri, path, *, description=None) -> None:
    registry.register_fragment(uri, path, description=description)
