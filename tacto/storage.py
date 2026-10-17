"""Private, atomic artifact writes shared by flows, candidates and traces."""

import os
import tempfile


def write_private(path: str, text: str) -> str:
    path = os.path.abspath(path)
    fd, temporary = tempfile.mkstemp(prefix=".tacto-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
