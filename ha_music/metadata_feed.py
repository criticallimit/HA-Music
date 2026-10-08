"""Shared metadata monitor for the currently selected broadcast station.

A single worker polls the broadcaster; browsers subscribe to updates over SSE.
"""
import json
import threading
import time
from metadata import now_playing, ensure_icy_worker

RADIO = frozenset(("1live", "wdr2", "swr3"))

class MetadataMonitor:
    def __init__(self):
        self.condition = threading.Condition()
        self.station = ""
        self.generation = 0
        self.sequence = 0
        self.latest = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="radio-metadata")
        self.thread.start()

    def select(self, station):
        if station not in RADIO and station != "":
            return
        with self.condition:
            if station != self.station:
                self.station = station
                self.generation += 1
                self.latest = None
                self.sequence += 1
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return self.sequence, self.station, self.latest

    def await_change(self, sequence, timeout=15):
        with self.condition:
            self.condition.wait_for(lambda: self.sequence != sequence, timeout=timeout)
            return self.sequence, self.station, self.latest

    def _run(self):
        while True:
            with self.condition:
                station = self.station
                generation = self.generation
                if not station:
                    self.condition.wait(timeout=30)
                    continue
            try:
                ensure_icy_worker(station)
                value = now_playing(station)
            except Exception:
                value = None
            with self.condition:
                if generation == self.generation and value is not None:
                    # Forward a new state only when data changes, not every poll.
                    if value != self.latest:
                        self.latest = value
                        self.sequence += 1
                        self.condition.notify_all()
                self.condition.wait_for(lambda: self.generation != generation, timeout=5)


MONITOR = MetadataMonitor()
