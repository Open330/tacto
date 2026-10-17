"""Flow store — the recipe library that makes replay token-efficient.

A flow is authored once (by a model, or by hand) and saved here as JSON. When a
request resolves to a stored flow, that flow's replay runs model-free (zero
tokens). Resolving a natural-language request to the right flow is a separate
step (see Agent.resolve) and is not guaranteed for every phrasing — a miss falls
back to the token-spending authoring path. So the accurate claim is "a resolved
flow's replay is model-free", not "every later run is free".
"""

from __future__ import annotations

import json
import os

from .flow import Flow
from .storage import write_private

def default_directory() -> str:
    """Source checkout keeps its recipes; installed packages use user data.

    A wheel contains no bundled recipe directory and must not create mutable
    data next to site-packages. Ignore relative XDG paths, as required by XDG's
    absolute-path convention.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    source_flows = os.path.join(root, "flows")
    if os.path.isfile(os.path.join(root, "pyproject.toml")) and os.path.isdir(source_flows):
        return source_flows
    data = os.environ.get("XDG_DATA_HOME", "")
    if not data or not os.path.isabs(data):
        data = os.path.join(os.path.expanduser("~"), ".local", "share")
    canonical = os.path.join(data, "tacto", "flows")
    legacy = os.path.join(data, "nlc", "flows")
    # Never move user data merely by importing or diagnosing the runtime.
    canonical_has_flows = os.path.isdir(canonical) and any(name.endswith(".json") for name in os.listdir(canonical))
    legacy_has_flows = os.path.isdir(legacy) and any(name.endswith(".json") for name in os.listdir(legacy))
    return legacy if legacy_has_flows and not canonical_has_flows else canonical


DEFAULT_DIR = default_directory()


class FlowStore:
    def __init__(self, path: str | None = None):
        self.path = default_directory() if path is None else os.fspath(path)
        if path is None and os.path.basename(os.path.dirname(self.path)) == "tacto":
            legacy = os.path.join(os.path.dirname(os.path.dirname(self.path)), "nlc", "flows")
            if os.path.isdir(legacy) and any(name.endswith(".json") for name in os.listdir(legacy)):
                raise self.MigrationRequired(f"Both flow stores contain data: {self.path} and {legacy}. Choose FlowStore(path=...) explicitly before migrating; no files were moved or overwritten.")
        os.makedirs(self.path, exist_ok=True)

    def _file(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "_-")
        return os.path.join(self.path, f"{safe}.json")

    def save(self, flow: Flow) -> str:
        p = self._file(flow.name)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    existing = json.load(f)
            except ValueError:
                existing = {}
            if isinstance(existing, dict) and existing.get("schema") == 1:
                raise self.MigrationRequired(f"{p}: migrate schema 1 explicitly; not overwritten")
        write_private(p, flow.to_json())
        return p

    class Corrupt(ValueError):
        """A file exists under the flow name but is not a usable Flow IR."""

    class MigrationRequired(ValueError):
        """A valid legacy artifact is neither missing nor disposable."""

    def load(self, name: str) -> Flow:
        path = self._file(name)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        try:
            legacy = json.loads(text).get("schema") == 1
        except (ValueError, AttributeError):
            legacy = False
        if legacy:
            raise self.MigrationRequired(f"{path}: schema 1 requires explicit migration")
        try:
            return Flow.from_json(text)
        except Exception as e:
            # A stored recipe can be damaged (a trace written over it, a partial
            # write). Say so clearly instead of surfacing a raw KeyError from
            # deep inside parsing — and let callers re-author rather than crash.
            raise FlowStore.Corrupt(
                f"{path} 은(는) 올바른 Flow IR이 아닙니다 ({e}). "
                f"파일을 지우고 다시 학습하거나 복구하세요.") from e

    def has(self, name: str) -> bool:
        """True only when the file exists AND parses — a damaged file is
        treated as absent so the caller can re-author over it."""
        path = self._file(name)
        if not os.path.exists(path):
            return False
        try:
            self.load(name)
            return True
        except self.MigrationRequired:
            raise
        except Exception:
            return False

    def list(self) -> list[str]:
        return sorted(f[:-5] for f in os.listdir(self.path) if f.endswith(".json"))
