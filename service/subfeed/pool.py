"""Bounded process pool with strict priority: play > detail > save > discovery.

Blocking work (yt-dlp, later ffmpeg) runs in worker processes so a hung extraction can never
stall the API event loop or hold a SQLite transaction. At most `workers` jobs run at once
(2 by default, which is also the "max 2 concurrent yt-dlp calls" rule). When a slot frees,
the lowest priority number waits least; equal priorities run first-come first-served.

DISCOVERY jobs are also paced: at most one starts every DISCOVERY_INTERVAL_S seconds across the
whole process, so a big backfill (hundreds of new channels after the first Google sync) trickles
out instead of bursting at YouTube from the home IP. The wait happens in the submitting thread
(the scheduler's pollers, the canary), never in a pool slot, so plays are not held up by it.
"""
from __future__ import annotations

import itertools
import multiprocessing
import queue
import threading
import time
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from typing import Callable

PLAY = 0
DETAIL = 1      # description/chapters for the focused card: user-facing, but never ahead of a play
SAVE = 2
DISCOVERY = 3

# Hard per-job deadlines in seconds. Saves have no hard cap (a 4K download is legitimately
# long); they are watched through running_jobs() ages instead.
DEADLINES = {PLAY: 60.0, DETAIL: 60.0, SAVE: None, DISCOVERY: 120.0}
DISCOVERY_INTERVAL_S = 4.0


class PriorityPool:
    def __init__(self, workers: int = 2, executor_factory: Callable[[int], Executor] | None = None):
        self.workers = workers
        self._factory = executor_factory or (
            lambda n: ProcessPoolExecutor(max_workers=n, mp_context=multiprocessing.get_context("spawn"))
        )
        self._executor: Executor | None = None
        self._generation = 0
        self._running: dict[int, tuple[int, float]] = {}   # job seq -> (priority, started monotonic)
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._closed = False
        self._pace_lock = threading.Lock()
        self._next_discovery = 0.0   # monotonic time the next DISCOVERY job may be queued

    def submit(self, priority: int, fn: Callable, *args, timeout: float | None = -1, **kwargs) -> Future:
        """timeout: seconds before the job is abandoned and the pool restarted; default is the
        priority's deadline, None means no cap."""
        if timeout == -1:
            timeout = DEADLINES.get(priority)
        if priority == DISCOVERY:
            self._pace()
        fut: Future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("pool is shut down")
            if not self._threads:
                self._executor = self._factory(self.workers)
                for i in range(self.workers):
                    t = threading.Thread(target=self._run, name=f"subfeed-pool-{i}", daemon=True)
                    t.start()
                    self._threads.append(t)
        self._q.put((priority, next(self._seq), fut, fn, (args, timeout), kwargs))
        return fut

    def _pace(self) -> None:
        """Reserve the next DISCOVERY slot under a lock, then sleep outside it until the slot."""
        interval = DISCOVERY_INTERVAL_S
        if interval <= 0:
            return
        with self._pace_lock:
            now = time.monotonic()
            slot = max(now, self._next_discovery)
            self._next_discovery = slot + interval
        if slot > now:
            time.sleep(slot - now)

    def running_jobs(self) -> list[dict]:
        """Heartbeat for health: priority and age in seconds of every job currently running."""
        now = time.monotonic()
        with self._lock:
            return [{"priority": p, "age_s": round(now - t, 1)} for p, t in self._running.values()]

    def _replace_executor(self, broken_generation: int) -> None:
        """Kill the worker processes and start a fresh executor, once per breakage."""
        with self._lock:
            if self._generation != broken_generation or self._closed:
                return  # another thread already replaced it
            old = self._executor
            self._executor = self._factory(self.workers)
            self._generation += 1
        if old is not None:
            for proc in list(getattr(old, "_processes", {}).values()):
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            old.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> None:
        while True:
            prio, seq, fut, fn, packed, kwargs = self._q.get()
            if fut is None:
                return
            args, timeout = packed
            if not fut.set_running_or_notify_cancel():
                continue
            with self._lock:
                self._running[seq] = (prio, time.monotonic())
            try:
                fut.set_result(self._execute(fn, args, kwargs, timeout))
            except BaseException as exc:  # noqa: BLE001 - hand every failure to the caller
                fut.set_exception(exc)
            finally:
                with self._lock:
                    self._running.pop(seq, None)

    def _execute(self, fn, args, kwargs, timeout):
        for attempt in (1, 2):
            with self._lock:
                executor, gen = self._executor, self._generation
            try:
                return executor.submit(fn, *args, **kwargs).result(timeout=timeout)
            except FutureTimeout:
                # a hung extraction: kill the workers (other in-flight jobs retry once) and report
                self._replace_executor(gen)
                raise TimeoutError(f"job exceeded its {timeout:g} s deadline") from None
            except BrokenProcessPool:
                # a worker died (OOM, crash): rebuild the pool and retry this job once
                self._replace_executor(gen)
                if attempt == 2:
                    raise

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            n = len(self._threads)
        for _ in range(n):  # sentinels sort after all real work
            self._q.put((10**6, next(self._seq), None, None, None, None))
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)


_default: PriorityPool | None = None
_default_lock = threading.Lock()


def get_pool() -> PriorityPool:
    global _default
    with _default_lock:
        if _default is None:
            _default = PriorityPool(workers=2)
        return _default
