"""Optional aggregate timing without storing UI content or action parameters."""
import functools
import time


class TimedDriver:
    METHODS = frozenset({"observe", "probe", "act", "wait_until", "identity", "selection"})

    def __init__(self, driver, *, clock=time.monotonic):
        self._driver = driver
        self._clock = clock
        self._calls = {}

    def __getattr__(self, name):
        value = getattr(self._driver, name)
        if name not in self.METHODS or not callable(value):
            return value

        @functools.wraps(value)
        def measured(*args, **kwargs):
            started = self._clock()
            failed = True
            try:
                result = value(*args, **kwargs)
                failed = False
                return result
            finally:
                seconds = max(0.0, self._clock() - started)
                held = self._calls.setdefault(name, {"count": 0, "seconds": 0.0, "exceptions": 0})
                held["count"] += 1
                held["seconds"] += seconds
                held["exceptions"] += int(failed)
        return measured

    def snapshot(self):
        return {"schema": "tacto-runtime-timing-v1", "clock": "monotonic",
                "scope": "calls_through_driver_wrapper; wait_until_includes_internal_polling",
                "calls": {name: {**stats, "seconds": round(stats["seconds"], 6)}
                          for name, stats in sorted(self._calls.items())},
                "includes_model_or_cli_startup": False}
