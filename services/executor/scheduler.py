import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from .bus import Bus, BusError, CommandAck, StepCommand, StepResult
from .models import RUN_COMPLETED, RUN_FAILED, STEP_COMPLETED, STEP_FAILED, RunStatus
from .run_dag import DispatchKey, RunDAGState
from .store import Store

log = logging.getLogger(__name__)

RETRY_DELAY_S = 1.0
DEFAULT_STEP_DURATION_S = 1.0
# How long past dispatch, in step durations, before an accepted step with no
# result is reconciled against its driver's state.
STEP_MAX_WAIT_MULTIPLIER = 1.5


@dataclass(frozen=True)
class Dispatch:
    """A ready step paired with the device that would run it."""

    run_state: RunDAGState
    step_id: str
    device_id: str

    @property
    def priority(self) -> DispatchKey:
        return self.run_state.dispatch_key(self.step_id)


class Scheduler:
    """Decides what runs when, across every active run.

    Run and device state live in memory and the database is written as a
    record. Every decision is made synchronously -- with no await between
    choosing a step and claiming its device -- so concurrent calls to
    handle_result cannot dispatch the same step twice or double-book a device.
    Sending commands and writing to the database happen afterwards, and
    concurrently, so one slow driver does not hold up the others.
    """

    def __init__(self, store: Store, bus: Bus) -> None:
        self.store = store
        self.bus = bus
        self._run_states: dict[str, RunDAGState] = {}
        # device_id -> step_id it is running, or waiting to retry after a refusal.
        self._device_claims: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def start(self, run_id: str) -> None:
        """Begin executing a run."""
        if not await self.store.start_run(run_id):
            log.warning("scheduler: run %s is not pending, so not starting it", run_id)
            return

        run = await self.store.get_run(run_id)
        steps = await self.store.list_steps(run_id)
        device_ids = sorted({step.device_id for step in steps})
        durations = await asyncio.gather(*(self._step_duration(d) for d in device_ids))
        self._run_states[run_id] = RunDAGState(run, steps, dict(zip(device_ids, durations)))

        log.info("scheduler: run %s started (%d steps)", run_id, len(steps))
        await self._dispatch_ready_steps()

    async def handle_result(self, result: StepResult) -> None:
        """Record that a driver finished a step and move the run on.

        May be called concurrently.
        """
        run_state = self._run_states.get(result.run_id)
        if run_state is None or not run_state.is_unfinished(result.step_id):
            log.warning(
                "scheduler: ignoring unexpected result for %s (step=%s run=%s)",
                result.step_name,
                result.step_id,
                result.run_id,
            )
            return

        if self._device_claims.get(result.device_id) == result.step_id:
            del self._device_claims[result.device_id]
        run_status = self._apply_result(run_state, result)

        # Yield once so every result that arrived in the same tick is applied
        # before any of them decides what runs next.
        # batches only results already delivered; a time window (a policy param)
        # would also catch near-simultaneous ones (future work).
        await asyncio.sleep(0)

        await asyncio.gather(
            self._dispatch_ready_steps(),
            self._record_result(result, run_status),
        )

    def _apply_result(self, run: RunDAGState, result: StepResult) -> RunStatus | None:
        """Update the run in memory. Returns the run's new status, if it has one."""
        run_status = None
        if result.error:
            log.warning("scheduler: %s failed: %s", result.step_name, result.error)
            if not run.failed:
                run_status = RUN_FAILED
            run.mark_failed(result.step_id)
        else:
            run.mark_finished(result.step_id)
            if run.is_complete:
                run_status = RUN_COMPLETED

        self._forget_run_if_finished(run)
        return run_status

    async def _record_result(self, result: StepResult, run_status: RunStatus | None) -> None:
        """Write the step before the run, so a finished run never shows unfinished steps."""
        step_status = STEP_FAILED if result.error else STEP_COMPLETED
        await self.store.record_step_finished(result.step_id, step_status, result.error)
        if run_status is not None:
            await self.store.finish_run(result.run_id, run_status)
            log.info("scheduler: run %s %s", result.run_id, run_status)

    async def _dispatch_ready_steps(self) -> None:
        dispatches = self._choose_dispatches()
        await asyncio.gather(*(self._send(dispatch) for dispatch in dispatches))

    def _choose_dispatches(self) -> list[Dispatch]:
        """Pick the best ready step for every unclaimed device, and claim it.

        Synchronous on purpose; see the class docstring.

        """
        candidates_by_device: dict[str, list[Dispatch]] = {}
        for run_state in self._run_states.values():
            if run_state.failed:
                continue
            for device_id in run_state.get_devices_with_ready_steps():
                if device_id in self._device_claims:
                    continue
                candidates_by_device.setdefault(device_id, []).extend(
                    Dispatch(run_state, step_id, device_id)
                    for step_id in run_state.get_ready_steps(device_id)
                )

        dispatches = []
        for device_id, candidates in candidates_by_device.items():
            # ponytail: scans every ready step of every run per decision; keep a
            # heap per (device, run) by tail if ready sets grow large.
            best = min(candidates, key=lambda candidate: candidate.priority)
            best.run_state.mark_dispatched(best.step_id)
            self._device_claims[device_id] = best.step_id
            dispatches.append(best)
        return dispatches

    async def _send(self, dispatch: Dispatch) -> None:
        command = StepCommand(
            run_id=dispatch.run_state.run_id,
            step_id=dispatch.step_id,
            step_name=dispatch.run_state.step_name(dispatch.step_id),
            device_id=dispatch.device_id,
        )
        try:
            ack = await self.bus.send_command(command)
        except BusError as exc:
            ack = CommandAck(accepted=False, reason=str(exc))

        if ack.accepted:
            max_wait_s = (
                dispatch.run_state.step_duration(dispatch.step_id) * STEP_MAX_WAIT_MULTIPLIER
            )
            await asyncio.gather(
                self._arm_overdue_check(dispatch, max_wait_s),
                self.store.record_step_dispatched(dispatch.step_id),
            )
            return

        # ponytail: fixed delay and no limit; refusals are not failures. Back off
        # or give up after N if drivers can stay unavailable.
        log.info(
            "scheduler: %s not taken by %s (%s), retrying in %.1fs",
            command.step_name,
            dispatch.device_id,
            ack.reason,
            RETRY_DELAY_S,
        )
        dispatch.run_state.return_to_ready(dispatch.step_id)
        self._forget_run_if_finished(dispatch.run_state)
        asyncio.get_running_loop().call_later(RETRY_DELAY_S, self._retry, dispatch)

    def _retry(self, dispatch: Dispatch) -> None:
        """Free a device held after a refusal, unless a result already freed it."""
        if self._device_claims.get(dispatch.device_id) == dispatch.step_id:
            del self._device_claims[dispatch.device_id]
        self._run_in_background(self._dispatch_ready_steps())

    async def _arm_overdue_check(self, dispatch: Dispatch, max_wait_s: float) -> None:
        """Note the driver's failure count, then check for a result after max_wait_s.

        Read just after the driver accepted: it cannot finish the step, and so
        cannot count a failure for it, until the step's duration has passed.
        """
        try:
            failed_before: int | None = (await self.bus.driver_state(dispatch.device_id)).failed
        except BusError as exc:
            log.warning("scheduler: no failure count from %s (%s)", dispatch.device_id, exc)
            failed_before = None
        asyncio.get_running_loop().call_later(
            max_wait_s, self._check_overdue, dispatch, max_wait_s, failed_before
        )

    def _check_overdue(
        self, dispatch: Dispatch, max_wait_s: float, failed_before: int | None
    ) -> None:
        if self._is_awaiting_result(dispatch):
            self._run_in_background(self._reconcile(dispatch, max_wait_s, failed_before))

    def _is_awaiting_result(self, dispatch: Dispatch) -> bool:
        return self._device_claims.get(dispatch.device_id) == dispatch.step_id

    async def _reconcile(
        self, dispatch: Dispatch, max_wait_s: float, failed_before: int | None
    ) -> None:
        """Resolve an accepted step whose result is overdue, from its driver's state.

        An idle driver that has executed the step did the work and lost the
        report. If its failure count rose meanwhile, only this step can have
        failed, so the step failed; otherwise it is taken as completed. Anything
        else is stuck, and fails the step.
        """
        try:
            state = await self.bus.driver_state(dispatch.device_id)
            if state.busy or dispatch.step_id not in state.executed:
                error = (
                    f"no result after {max_wait_s:.1f}s and driver busy={state.busy}, "
                    f"executed step={dispatch.step_id in state.executed}"
                )
            elif failed_before is not None and state.failed > failed_before:
                error = "result lost, and the driver counted a failure during this step"
            else:
                error = ""
        except BusError as exc:
            error = f"no result after {max_wait_s:.1f}s and no driver state: {exc}"

        # A real result may have landed while we waited for the driver.
        if not self._is_awaiting_result(dispatch):
            return

        step_name = dispatch.run_state.step_name(dispatch.step_id)
        if error:
            log.warning("scheduler: failing %s on %s: %s", step_name, dispatch.device_id, error)
        else:
            # ponytail: assumes success when the failure count is unknown.
            log.warning(
                "scheduler: result for %s on %s lost, assuming completed",
                step_name,
                dispatch.device_id,
            )
        await self.handle_result(
            StepResult(
                run_id=dispatch.run_state.run_id,
                step_id=dispatch.step_id,
                step_name=step_name,
                device_id=dispatch.device_id,
                error=error,
            )
        )

    def _forget_run_if_finished(self, run: RunDAGState) -> None:
        if run.is_finished:
            self._run_states.pop(run.run_id, None)

    async def _step_duration(self, device_id: str) -> float:
        """How long the device takes per step, as its driver reports it."""
        try:
            state = await self.bus.driver_state(device_id)
        except BusError as exc:
            log.warning("scheduler: no step duration from %s (%s)", device_id, exc)
            return DEFAULT_STEP_DURATION_S
        return state.step_duration_s or DEFAULT_STEP_DURATION_S

    def _run_in_background(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Fire-and-forget a coroutine, keeping a reference and logging any failure."""
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(_log_task_failure)


def _log_task_failure(task: "asyncio.Task[None]") -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("scheduler: background dispatch raised: %r", task.exception())
