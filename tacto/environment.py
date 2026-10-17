"""Read-only legacy environment lookup, with canonical TACTO values preferred."""
from __future__ import annotations
import os

# Only runtime configuration names with existing semantics are accepted. Signing
# teams/requirements are intentionally absent: environment cannot grant trust.
RUNTIME_KEYS = ('AUTHOR_DEBUG', 'EVENT_MARKER', 'HELPER_FD', 'HELPER_TOKEN',
                'LAUNCHER', 'NO_INDICATOR')


def value(key, default=None, *, environ=None):
    if key not in RUNTIME_KEYS:
        raise KeyError(key)
    env = os.environ if environ is None else environ
    return env.get('TACTO_' + key, env.get('NLC_' + key, default))
