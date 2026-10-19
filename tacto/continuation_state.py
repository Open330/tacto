"""Private, in-memory data for one checked continuation, never a saved recipe."""
from copy import deepcopy


class ReplayContinuation:
    __slots__ = ('step_index', '_captures', '_secrets', '_sensitive_names', '_checkpoint', '_outcome')

    def __init__(self, *, step_index, captures, secrets, sensitive_names=(), checkpoint, outcome):
        self.step_index=step_index
        self._captures=deepcopy(captures)
        self._secrets=tuple(secrets)
        self._sensitive_names=frozenset(sensitive_names)
        self._checkpoint=deepcopy(checkpoint)
        self._outcome=deepcopy(outcome)

    def __repr__(self):
        return '<ReplayContinuation: private in-memory state>'

    def __getstate__(self):
        raise TypeError('continuation state cannot be serialized')

    def resume_values(self):
        return deepcopy(self._captures), tuple(self._secrets), self._sensitive_names

    def predicates(self):
        return deepcopy(self._checkpoint), deepcopy(self._outcome)

    def clear(self):
        """Release references; this is not a claim of secure memory erasure."""
        self._captures.clear()
        self._secrets=()
        self._sensitive_names=frozenset()
        self._checkpoint=None
        self._outcome=None
