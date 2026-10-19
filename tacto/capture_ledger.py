"""Bounded process-local spent IDs; no image retention or safe-entry eviction.

An uncertain attempt stays spent after object collection. This is not durable
across worker restart and does not establish operation completion.
"""
from threading import Lock


class CaptureLedger:
    def __init__(self, capacity=16384):
        if type(capacity) is not int or not 1 <= capacity <= 16384:
            raise ValueError('invalid capture ledger capacity')
        self.capacity = capacity
        self._ids = set()
        self._lock = Lock()

    def reserve(self, capture_id):
        if type(capture_id) is not str or not 0 < len(capture_id) <= 128:
            return 'invalid_capture_id'
        with self._lock:
            if capture_id in self._ids:
                return 'capture_already_used'
            if len(self._ids) >= self.capacity:
                return 'capture_ledger_full'
            self._ids.add(capture_id)
        return None
