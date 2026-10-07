"""Put one question to a model, on the outside of the permission boundary.

The signed launcher gives its child a fixed PATH so that nothing the child
holds — and it holds screen content, which is the untrusted part — can choose
what runs. That also puts the model CLIs, which live in user directories, out
of the child's reach. Learning already lived on the far side of this; so does
every question the screen loop asks.

So the launcher runs this instead, in its own environment, and relays the text
back. --result-envelope additionally returns observed model metadata; --json
still controls the model answer format. Structured error/timeout envelopes exit
zero so the relay can read their status. This process has no helper channel and no fd 3: it reads a prompt on
stdin and writes an answer on stdout, and cannot touch the screen whatever the
prompt tells it to do.
"""

from __future__ import annotations

import sys
import math
import json


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-envelope", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--result-envelope", action="store_true")
    parser.add_argument("--provider", default="auto")
    parser.add_argument("--timeout", type=float, default=420)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 420:
        parser.error("--timeout must be finite, positive, and at most 420")
    prompt = sys.stdin.read()
    if not prompt.strip():
        return 1
    if args.result_envelope:
        from .authoring import ask_model_result
        try:
            if args.image_envelope:
                from .provider_images import decode_image, validate_model
                if len(prompt.encode('utf8')) > 8 * 1024 * 1024 or args.provider not in ('claude', 'codex'):
                    raise ValueError('invalid image request')
                envelope = json.loads(prompt)
                if not isinstance(envelope, dict) or set(envelope) != {'prompt', 'image', 'model'}:
                    raise ValueError('invalid image request')
                question = envelope['prompt']
                if not isinstance(question, str) or not question.strip() or len(question.encode('utf8')) > 400000:
                    raise ValueError('invalid image prompt')
                png = decode_image(envelope['image']); model = validate_model(envelope['model'])
                if args.provider == 'claude':
                    from .claude_result import claude_result
                    result = claude_result(question, model=model, timeout=args.timeout, image_bytes=png)
                else:
                    from .codex_result import codex_result
                    result = codex_result(question, model=model, timeout=args.timeout, image_bytes=png)
            else:
                result = ask_model_result(prompt, args.provider, timeout=args.timeout,
                                          expect_json=args.json)
            encoded = json.dumps(result.to_dict(), ensure_ascii=False, allow_nan=False)
        except Exception:
            # Unexpected transport/serialization failures carry no raw prompt,
            # provider stderr or exception text across this boundary.
            sys.stderr.write("nlc.ask_provider: model envelope unavailable\n")
            return 1
        sys.stdout.write(encoded)
        return 0  # Structured error/timeout belongs to the envelope, not exit status.
    if args.image_envelope:
        parser.error("--image-envelope requires --result-envelope")
    from .authoring import _ask_model
    try:
        text = _ask_model(prompt, args.provider, timeout=args.timeout, expect_json=args.json)
    except TimeoutError:
        sys.stderr.write("nlc.ask_provider: model deadline exceeded\n")
        return 124
    except Exception as e:
        sys.stderr.write(f"nlc.ask_provider: {e}\n")
        return 1
    if not text:
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
