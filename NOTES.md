# Workflow Executor Task Notes

**Author:** James Adamson

**Time spent:** 1 working day (~8 hours)


## Problem Framing

At the core of this problem is a job-shop scheduling problem with precedence constraints. More generally, it's an example of a sequential decision problem, where decisions must be made over time and under uncertainty. Given this framing, I find it useful to reference taxonomy from Warren Powell's [Sequential Decision Analytics Framework](https://warrenpowell.org/sequential-decision-problems/). 

**Decisions**: When steps should run, and in what sequence.

**KPIs**: Complete runs in the shortest possible time. Ambiguous over multiple concurrent runs

**Uncertainties**:
- Steps can fail with some probability.
- New runs can be started in the future at an unknown time
- The duration of steps is unlikely to be purely deterministic. This is my inference, nothing it the task or code says there is uncertainty (fixed values are given in `docker-compose.yml`). But I'd expect in the real-world there would be at least some jitter.

**Constraints**:
- Precedence constraints between steps
- An instrument can only run a single step at a time

## Q1 What did you deliberately not build, and why?

- **Better `*_FAIL_PCT` handling**: Currently the code will fail the run to prevent hanging, letting in-flight steps finish. Not implemented step retries yet because it's non-breaking and prioritised handling dropped results instead.
- **DAG caching**: If many runs share the same workflow, we could benefit from caching the DAG so we don't have to rebuild it and run the topological sort at the start of each new run
- **Restart recovery**: scheduler state is in-memory, so an executor restart orphans in-flight runs. Recovery would rebuild runs from the DB and reconcile in-flight steps against driver_state
- **Planner layer (look-ahead)**: Not built. A real-time dispatcher is needed regardless to handle unexpected events at low latency, so I built that first. A planner could be either an optimiser (e.g. using CP-SAT, Hexaly, or Timefold) or a learned optimisation proxy trained in simulation. Either would probably only pay off with richer constraints such as time lags or shared resources, and a simple heuristic dispatcher is the baseline it would need to beat. If added, solves should run asynchronously, never inside start or handle_result to avoid delaying execution. The sequences should influence priorities in the dispatcher.
- **A stochastic simulator**: I would prioritize early on building a simulator to evaluate, compare, and tune decision policies outside of production. But I do not think this fits into the scope of this exercise.
- **More advanced dispatch policy**: The policy for choosing what to dispatch could be improved with tuning, a possible lookahead, or even a policy learnt from offline optimisation solves (imitation learning). But I'd favour simplicity until something could be proved in a simulator.


## Q2 Where is your implementation most likely to break?

I would say these are the top three concerns for breaking the current implementation:

1. A step could potentially run twice. `bus.send_command(command)` with a timeout gets treated as a refusal, but the driver may have actually accepted the step. **Fix:** Verify against `DriverState.executed` before re-queuing the step and consider calling `_arm_overdue_check` when getting the timeout to check for the result getting dropped
2. (As in Q1) the executor restarting and losing the scheduler's in-memory state. **Fix:** building recovery of runs from the DB and reconciling against driver state 
3. The database can drift from the scheduler's in-memory state. Memory is the source of truth and the database is only a record, so each result updates memory before it is written. If a write fails, the error is logged but not retried. Runs still execute correctly, but the record is wrong: a finished run can show as `running` forever, or a step can stay `dispatched`. **Fix:** Retry writes until they succeed (with limits), and enable connection checks on the pool so stale connections are replaced rather than failing writes.


## Q3 If two drivers report a step finished at the same moment, what happens in your code?

`handle_result` frees the device and updates the run's DAG in memory, then yields once with `asyncio.sleep(0)`. Every result delivered in the same event-loop tick is therefore applied before any of them makes a dispatch decision. The first decision sees all the newly ready steps, not just those from its own result.

## Q4 An instrument does the work and never reports back (`*_DROP_PCT`). What does your code do, and what should it do?

When dispatching a step, the scheduler sets a deadline for `step_duration * STEP_MAX_WAIT_MULTIPLIER`. It checks for a result after that time. If it never reports back, it checks the `DriverState` to see if the driver has become free and the step listed as executed. If not, it assumes the step has got stuck / failed and fails the run. Otherwise, it determines if the step was a success or failure based on the delta of the `failed` counter in the `DriverState`. If that incremented, it is considered a failed step. Otherwise, it's assumed to have completed and the run proceeds. 

**What else should it do?**
It's still possible that we could get a `BusError` trying to access the failed counter on the driver so a lost failure could still be treated as a success. Arguably, if we cannot get that information it is less risky to fail the run. 


## My thoughts on the exercise

- It was an enjoyable exercise with interesting problems to think about. I liked how it gave a bit of freedom to do your own thing with it
- The brief was mostly very clear. The one exception was that there was no mention of what KPIs the lab cared about (mean flow rate, batch makespan etc) and whether queuing should be FIFO or not. This is important for how the scheduler should behave. Perhaps this was intentional ambiguity though
- I did spend a fair bit more than 2 hours. This was largely my choice to give it a good go. I think sticking to 2 hours would require someone to be very strict
- I probably spend more time thinking about the decision logic than the task intended. That's because I find that interesting and was thinking of different ways to take it, now and in the future

## AI Usage

I used Claud Code to help with this exercise, primarily in helping with writing code to work faster. `AI_SESSIONS.md` gives an AI summary of the two main sessions. Raw exports can be provided if helpful. This doesn't cover all work as there were several manual changes outside of Claud as well as some other minor sessions.