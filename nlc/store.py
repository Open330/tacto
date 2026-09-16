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

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "flows")


class FlowStore:
    def __init__(self, path: str = DEFAULT_DIR):
        self.path = path
        os.makedirs(path, exist_ok=True)

    def _file(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "_-")
        return os.path.join(self.path, f"{safe}.json")

    def save(self, flow: Flow) -> str:
        p = self._file(flow.name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(flow.to_json())
        return p

    def load(self, name: str) -> Flow:
        with open(self._file(name), encoding="utf-8") as f:
            return Flow.from_json(f.read())

    def has(self, name: str) -> bool:
        return os.path.exists(self._file(name))

    def list(self) -> list[str]:
        return sorted(f[:-5] for f in os.listdir(self.path) if f.endswith(".json"))
