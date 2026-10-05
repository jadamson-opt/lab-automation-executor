from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

RunStatus = Literal["pending", "running", "completed", "failed", "aborted"]

RUN_PENDING: Final = "pending"
RUN_RUNNING: Final = "running"
RUN_COMPLETED: Final = "completed"
RUN_FAILED: Final = "failed"
RUN_ABORTED: Final = "aborted"

StepStatus = Literal["pending", "dispatched", "running", "completed", "failed"]

STEP_PENDING: Final = "pending"
STEP_DISPATCHED: Final = "dispatched"
STEP_RUNNING: Final = "running"
STEP_COMPLETED: Final = "completed"
STEP_FAILED: Final = "failed"


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    type: str


@dataclass(frozen=True)
class Run:
    id: str
    workflow_name: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class Step:
    id: str
    run_id: str
    name: str
    device_id: str
    status: StepStatus
    depends_on: list[str]
    dispatch_count: int
    dispatched_at: datetime | None
    finished_at: datetime | None
    error: str | None
