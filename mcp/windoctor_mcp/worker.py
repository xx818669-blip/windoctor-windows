"""One physical same-process worker; cancellation never frees a running slot."""

import asyncio
from dataclasses import dataclass
import threading

import anyio

from .errors import adapter_error

APPLICATION_TIMEOUT = 10.0


@dataclass
class Job:
    done: anyio.Event
    result: object = None


class WorkSlot:
    def __init__(self):
        self.job = None
        self.state = "idle"
        self.thread = None

    def complete(self, job, result):
        job.result = result
        if self.job is job:
            self.job = None
            self.state = "idle"
        job.done.set()

    def execute(self, loop, job, operation):
        try:
            result = operation()
        except BaseException:
            result = adapter_error("A_INTERNAL", "capture", "/result")
        # Only actual physical completion schedules release. A closed event
        # loop means shutdown; this non-daemon worker still finishes normally.
        try:
            loop.call_soon_threadsafe(self.complete, job, result)
        except RuntimeError:
            pass

    async def run(self, operation):
        if self.job is not None:
            return adapter_error("A_BUSY")
        job = Job(anyio.Event())
        self.job, self.state = job, "running"
        self.thread = threading.Thread(target=self.execute,
            args=(asyncio.get_running_loop(), job, operation), name="windoctor-capture", daemon=False)
        try:
            self.thread.start()
        except Exception:
            self.job, self.state = None, "idle"
            return adapter_error("A_INTERNAL", "capture", "/result")
        try:
            with anyio.move_on_after(APPLICATION_TIMEOUT) as deadline:
                await job.done.wait()
            if deadline.cancelled_caught:
                if self.job is job:
                    self.state = "draining"
                return adapter_error("A_TIMEOUT", "capture", "/request")
            return job.result
        except anyio.get_cancelled_exc_class():
            if self.job is job:
                self.state = "draining"
            raise
