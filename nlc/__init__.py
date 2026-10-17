"""Legacy import name for the ``tacto`` package.

``import nlc.flow`` returns the very module object ``import tacto.flow``
returns, whichever is imported first, so classes, singletons and
``unittest.mock.patch("nlc.helper.X")`` all act on the canonical module.
Nothing here imports a ``tacto`` submodule until that submodule is asked for:
importing ``nlc`` loads only the (empty) ``tacto`` package.

``python -m nlc.cli`` runs ``tacto/cli.py`` as ``__main__`` exactly like
``python -m tacto.cli``.
"""
import importlib
import importlib.machinery
import importlib.util
import sys

_LEGACY = __name__          # "nlc"
_CANONICAL = "tacto"


def _canonical_name(fullname):
    return _CANONICAL + fullname[len(_LEGACY):]


class _AliasLoader:
    """Hands back the canonical module instead of executing a second copy.

    Plain classes rather than importlib.abc bases: that module pulls in
    importlib.resources, tempfile and compression modules on first use.
    """

    def __init__(self, canonical, canonical_spec):
        self._canonical = canonical
        self._spec = canonical_spec

    def create_module(self, spec):
        module = importlib.import_module(self._canonical)
        self._canonical_spec = module.__spec__
        return module

    def exec_module(self, module):
        # Already executed once, as the canonical module. The import system
        # has just stamped the legacy spec onto it; put the canonical one
        # back so reload, pickling and runpy keep seeing tacto.x.
        module.__spec__ = self._canonical_spec

    # runpy (`python -m nlc.x`) asks the loader for code to run as __main__.
    def get_code(self, fullname):
        return self._spec.loader.get_code(self._canonical)

    def get_source(self, fullname):
        return self._spec.loader.get_source(self._canonical)

    def is_package(self, fullname):
        return self._spec.submodule_search_locations is not None

    def get_filename(self, fullname):
        return self._spec.origin


class _AliasFinder:
    _tacto_legacy_alias = True     # survives importlib.reload(nlc), unlike the class

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_LEGACY + "."):
            return None
        canonical = _canonical_name(fullname)
        canonical_spec = importlib.util.find_spec(canonical)
        if canonical_spec is None:
            return None
        spec = importlib.machinery.ModuleSpec(
            fullname, _AliasLoader(canonical, canonical_spec),
            origin=canonical_spec.origin,
            is_package=canonical_spec.submodule_search_locations is not None)
        spec.has_location = canonical_spec.has_location
        return spec


_tacto = importlib.import_module(_CANONICAL)
# PyPI has an unrelated `tacto` distribution that installs the same top-level
# package. Alias nothing unless this is the Tacto runtime; the marker detects
# an accidental collision, it is not a proof of authenticity.
if getattr(_tacto, "__tacto_runtime__", None) is not True:
    raise ImportError(
        "nlc: the importable 'tacto' package is not the Tacto runtime "
        f"(tacto-runtime); found {getattr(_tacto, '__file__', None)!r}. Another "
        "distribution named 'tacto' occupies the same namespace. Install "
        "tacto-runtime in an isolated environment without it.",
        name=_LEGACY)

_FINDER = _AliasFinder()
if not any(getattr(f, "_tacto_legacy_alias", False) for f in sys.meta_path):
    # First, so `nlc.x` never falls through to a path search that could
    # execute a second copy of a module under the legacy name.
    sys.meta_path.insert(0, _FINDER)

# This package has no modules of its own; submodules resolve only through the
# finder above.
__path__ = []


def __getattr__(name):
    # `nlc.<attr>` for anything the canonical package exposes, including a
    # submodule that was already imported as `tacto.<name>`.
    try:
        return getattr(_tacto, name)
    except AttributeError:
        raise AttributeError(f"module {_LEGACY!r} has no attribute {name!r}") from None
