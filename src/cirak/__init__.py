from importlib.metadata import PackageNotFoundError, version

from .api import check, resolve, run
from .build import Graph, GraphNode
from .errors import (
    BuildError,
    CirakError,
    CirakWarning,
    ConfigError,
    Problem,
    RegistryError,
    Source,
)
from .registry import Registry, register, register_fragment, register_many
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
    "Graph",
    "GraphNode",
    "Problem",
    "Registry",
    "RegistryError",
    "Source",
    "__version__",
    "check",
    "register",
    "register_fragment",
    "register_many",
    "register_std",
    "resolve",
    "run",
]
