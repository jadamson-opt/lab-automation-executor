# Workflow Executor Task Notes

**Author:** James Adamson

**Time spend:** 


## Problem Framing

At the core of this problem is a job-shop scheduling problem with precedence constraints. More generally, it's an example of a sequential decision problem, where decisions must be made over time and under uncertainty. Given this framing, I find it useful to reference taxonomy from Warren Powell's [Sequential Decision Analytics Framework](https://warrenpowell.org/sequential-decision-problems/). 

**Decisions**: When steps should run, and in what sequence.

**KPIs**: Complete workflows in the shortest possible time

**Uncertainties**:
- Steps can fail with some probability.
- The duration of steps is unlikely to be purely deterministic. This is my inference, nothing it the task or code says there is uncertainty (fixed values are given in `docker-compose.yml`). But I'd expect in the real-world there would be at least some jitter.

**Constraints**:
- Precedence constraints between steps
- An instrument can only run a single step at a time

## What I chose not to build yet

- **Multiple layers of decision-making:** For this problem, with more time I'd consider two layers of decision-making policies:
   1. Execution/dispatch: Directly controls instruments. Must be able to react quickly to changing events and uncertainty. **BUILT FOR THIS EXERCISE**.
   2. Planner: Looks ahead to create a more informed and optimal sequence of steps. Possibly through solving an optimization problem (e.g. with a solver like CP-SAT, Timefold, or Hexaly), or possibly through a learned approach (see future work). This could influence the execution layer. **NOT BUILDING YET**, because:
      1. A static scheduler couldn't entirely replace the dispatcher, as something would still need to handle events occurring unexpectedly and with low latency. So a real-time dispatcher **must** be built regardless
      2. May have limited benefit for the simple problem as described. There would be more value if there were additional constraints like time-lags (min time gaps after completing steps) or other resources. Regardless, it's value ought to be demonstrated through simulation and a simple heuristic dispatcher should be the baseline to compare against.
      3. I would not want potentially costly optimisation solves to delay execution. So I don't think the provided `start` and `handle_result` endpoints are the right place for such solves. If used, they ought to be asynchronous and possibly only when new workflows are created or edited, or triggered by selected events only. I consider this out of scope for this exercise.
- **A simulator**: I would prioritize early on building a simulator to evaluate, compare, and tune decision policies outside of production. But I do not think this fits into the scope of this exercise.
- 


## Where is the implementation most likely to break

## Handling simultaneous events

## Handling missing messages

## Future work