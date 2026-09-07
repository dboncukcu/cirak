from importlib.metadata import PackageNotFoundError, version

from .api import check, flow_dump, layers, resolve, run
from .build import Deferred, Graph, GraphNode
from .errors import (
    BuildError,
    CirakError,
    CirakWarning,
    ConfigError,
    Problem,
    RegistryError,
    Source,
)
from .registry import Facts, Registry, declare_kinds, register, register_fragment, register_many
from .std import register_std

try:
    __version__ = version("cirak")
except PackageNotFoundError:
    __version__ = None

__all__ = [
    "BuildError",
    "CirakError",
    "CirakWarning",
    "ConfigError",
    "Deferred",
    "Facts",
    "Graph",
    "GraphNode",
    "Problem",
    "Registry",
    "RegistryError",
    "Source",
    "__version__",
    "check",
    "declare_kinds",
    "flow_dump",
    "layers",
    "register",
    "register_fragment",
    "register_many",
    "register_std",
    "resolve",
    "run",
]
