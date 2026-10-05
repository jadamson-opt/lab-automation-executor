from collections import deque
from datetime import datetime

from .models import Run, Step

DispatchKey = tuple[bool, float, float, datetime, str]


class RunDAGState:
    """One run's workflow DAG, and how far through it the run has got.

    Pure in-memory state with no I/O, so every method is synchronous. Steps are
    tracked by id and move pending -> ready -> in flight -> finished. A failed
    step counts as finished, and marks the run failed.
    """

    def __init__(
        self, run: Run, steps: list[Step], step_duration_by_device: dict[str, float]
    ) -> None:
        self.run_id = run.id
        self.created_at = run.created_at
        self.failed = False

        self._name_by_step = {step.id: step.name for step in steps}
        self._device_by_step = {step.id: step.device_id for step in steps}
        self._duration_by_step = {
            step.id: step_duration_by_device[step.device_id] for step in steps
        }

        step_id_by_name = {step.name: step.id for step in steps}
        self._successors: dict[str, list[str]] = {step.id: [] for step in steps}
        for step in steps:
            for dependency in step.depends_on:
                self._successors[step_id_by_name[dependency]].append(step.id)
        self._unfinished_predecessor_count = {step.id: len(step.depends_on) for step in steps}

        self._tail_by_step = self._compute_tails()

        self._ready_by_device: dict[str, set[str]] = {}
        self._in_flight: set[str] = set()
        self._finished: set[str] = set()
        for step_id, count in self._unfinished_predecessor_count.items():
            if count == 0:
                self._add_ready(step_id)

    def step_name(self, step_id: str) -> str:
        return self._name_by_step[step_id]

    def step_duration(self, step_id: str) -> float:
        return self._duration_by_step[step_id]

    def get_devices_with_ready_steps(self) -> list[str]:
        return [device_id for device_id, steps in self._ready_by_device.items() if steps]

    def get_ready_steps(self, device_id: str) -> set[str]:
        return self._ready_by_device.get(device_id, set())

    def is_unfinished(self, step_id: str) -> bool:
        return step_id in self._name_by_step and step_id not in self._finished

    @property
    def is_complete(self) -> bool:
        return not self.failed and len(self._finished) == len(self._name_by_step)

    @property
    def is_finished(self) -> bool:
        """Complete, or failed with nothing left running."""
        return self.is_complete or (self.failed and not self._in_flight)

    def mark_dispatched(self, step_id: str) -> None:
        self._ready_by_device[self._device_by_step[step_id]].remove(step_id)
        self._in_flight.add(step_id)

    def return_to_ready(self, step_id: str) -> None:
        """Undo a dispatch the driver refused. No-op if the step has moved on."""
        if step_id in self._in_flight:
            self._in_flight.remove(step_id)
            self._add_ready(step_id)

    def mark_finished(self, step_id: str) -> None:
        """Record a step that succeeded, and make ready any successor it unblocks.

        The step may still be in the ready set: a send that timed out can have
        been accepted after all.
        """
        self._remove_from_frontier(step_id)
        self._finished.add(step_id)
        for successor in self._successors[step_id]:
            self._unfinished_predecessor_count[successor] -= 1
            if self._unfinished_predecessor_count[successor] == 0:
                self._add_ready(successor)

    def mark_failed(self, step_id: str) -> None:
        self._remove_from_frontier(step_id)
        self._finished.add(step_id)
        self.failed = True

    def remaining_tail(self) -> float:
        """Time left on the run's critical path, counting in-flight steps in full.

        Only ready and in-flight steps (the frontier) need checking: every step not
        yet ready has an unfinished ancestor with a longer tail.
        """
        frontier = self._in_flight.union(*self._ready_by_device.values())
        return max((self._tail_by_step[step_id] for step_id in frontier), default=0.0)

    def dispatch_key(self, step_id: str) -> DispatchKey:
        """Lower dispatches first. Compared across runs competing for a device.
        This dispatch ranking is based on wanting to minimise mean flow time.

        1. Critical before non-critical: a step whose slack covers its own
           duration can wait one turn without delaying its run.
        2. Shortest remaining run first, so long runs do not hold up short ones.
        3. Least slack first.
        4. Oldest run first, to finish runs rather than interleave them.
        5. Step id, for determinism.
        """
        remaining_tail = self.remaining_tail()
        slack = remaining_tail - self._tail_by_step[step_id]
        return (
            slack >= self._duration_by_step[step_id],
            remaining_tail,
            slack,
            self.created_at,
            step_id,
        )

    def _add_ready(self, step_id: str) -> None:
        self._ready_by_device.setdefault(self._device_by_step[step_id], set()).add(step_id)

    def _remove_from_frontier(self, step_id: str) -> None:
        self._in_flight.discard(step_id)
        self._ready_by_device.get(self._device_by_step[step_id], set()).discard(step_id)

    def _compute_tails(self) -> dict[str, float]:
        """Longest duration-weighted path from each step to the end of the DAG."""
        tail_by_step: dict[str, float] = {}
        for step_id in reversed(self._topological_order()):
            longest_successor_tail = max(
                (tail_by_step[successor] for successor in self._successors[step_id]), default=0.0
            )
            tail_by_step[step_id] = self._duration_by_step[step_id] + longest_successor_tail
        return tail_by_step

    def _topological_order(self) -> list[str]:
        """
        Kahn's algorithm. The workflow loader has already rejected cycles.

        Returns a list of ordered step IDs, where the sequence abides by the
        precedences defined in the DAG.
        """
        remaining_predecessors = dict(self._unfinished_predecessor_count)
        queue = deque(step_id for step_id, count in remaining_predecessors.items() if count == 0)
        order: list[str] = []
        while queue:
            step_id = queue.popleft()
            order.append(step_id)
            for successor in self._successors[step_id]:
                remaining_predecessors[successor] -= 1
                if remaining_predecessors[successor] == 0:
                    queue.append(successor)
        return order
