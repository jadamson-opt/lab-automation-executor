"""The dispatch rule on the default workflow, without a database or drivers."""

from datetime import UTC, datetime, timedelta

from services.executor.models import Run, Step
from services.executor.run_dag import RunDAGState

LIQUID_HANDLER = "liquid-handler-1"
STEP_DURATIONS = {LIQUID_HANDLER: 2.0, "incubator-1": 2.0, "plate-reader-1": 2.0}

PCR = [
    ("fill_sample_plate", LIQUID_HANDLER, []),
    ("incubate_samples", "incubator-1", ["fill_sample_plate"]),
    ("fill_reagent_plate", LIQUID_HANDLER, ["fill_sample_plate"]),
    ("fill_buffer_plate", LIQUID_HANDLER, ["fill_sample_plate"]),
    ("warm_reagent_plate", "incubator-1", ["fill_reagent_plate"]),
    ("combine", LIQUID_HANDLER, ["incubate_samples", "warm_reagent_plate", "fill_buffer_plate"]),
    ("read_plate", "plate-reader-1", ["combine"]),
]


def make_run(run_id: str, created_at: datetime) -> RunDAGState:
    run = Run(run_id, "PCR Amplification", "running", created_at, created_at, None)
    steps = [
        Step(
            f"{run_id}/{name}", run_id, name, device_id, "pending", depends_on, 0, None, None, None
        )
        for name, device_id, depends_on in PCR
    ]
    return RunDAGState(run, steps, STEP_DURATIONS)


def best_on_liquid_handler(*runs: RunDAGState) -> str:
    candidates = [(run, step_id) for run in runs for step_id in run.get_ready_steps(LIQUID_HANDLER)]
    _, best_step_id = min(candidates, key=lambda candidate: candidate[0].dispatch_key(candidate[1]))
    return best_step_id


def test_critical_path_goes_first_and_shorter_run_wins() -> None:
    now = datetime.now(UTC)
    older = make_run("older", now)
    assert older.remaining_tail() == 10.0

    older.mark_dispatched("older/fill_sample_plate")
    older.mark_finished("older/fill_sample_plate")
    # fill_reagent_plate leads to warm_reagent_plate, so it is on the critical path.
    assert best_on_liquid_handler(older) == "older/fill_reagent_plate"

    # A newer run with less left to do takes the device ahead of the older one.
    newer = make_run("newer", now + timedelta(seconds=1))
    for name in [
        "fill_sample_plate",
        "fill_reagent_plate",
        "fill_buffer_plate",
        "incubate_samples",
        "warm_reagent_plate",
    ]:
        newer.mark_finished(f"newer/{name}")
    assert newer.remaining_tail() == 4.0
    assert best_on_liquid_handler(older, newer) == "newer/combine"
