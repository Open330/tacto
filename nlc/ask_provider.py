"""Put one question to a model, on the outside of the permission boundary.

The signed launcher gives its child a fixed PATH so that nothing the child
holds — and it holds screen content, which is the untrusted part — can choose
what runs. That also puts the model CLIs, which live in user directories, out
of the child's reach. Learning already lived on the far side of this; so does
every question the screen loop asks.

So the launcher runs this instead, in its own environment, and relays the text
back. This process has no helper channel and no fd 3: it reads a prompt on
stdin and writes an answer on stdout, and cannot touch the screen whatever the
prompt tells it to do.
"""

from __future__ import annotations

import sys


def main() -> int:
    want_json = "--json" in sys.argv[1:]
    prompt = sys.stdin.read()
    if not prompt.strip():
        return 1
    from .authoring import _ask_model
    try:
        text = _ask_model(prompt, "auto", expect_json=want_json)
    except Exception as e:
        sys.stderr.write(f"nlc.ask_provider: {e}\n")
        return 1
    if not text:
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
