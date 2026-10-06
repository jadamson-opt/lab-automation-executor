# AI-assisted development: session summary

This summary was written by Claude (Anthropic's coding assistant) from the exported transcripts of two working sessions with the user. It describes how the user and Claude worked together.

**Conventions.** Text in quote marks or blockquotes is verbatim from the export, with the user's typos left in. Anything else describing what the user said is a paraphrase, and is marked *(paraphrase)*.

## Overview of the working pattern

- The user set out the problem and a proposed design before any code was written, and explicitly told Claude not to edit or plan until the design had been discussed.
- Claude read the code, reported what it implied for the user's proposals, agreed with most of them, and disagreed with a few. It asked multiple-choice questions at decision points.
- The user made the design decisions. Several came from the user rather than Claude: the cross-run dispatch rule, driver-reported step durations, and the dropped-result deadline multiplier.
- The user corrected Claude's wording, a design simplification, and a test-hygiene shortcut. Claude changed course each time.
- In session 2, the user asked for a failing test before each fix. Claude wrote the test, confirmed it failed, then implemented the change.
- Claude verified changes against the running Docker stack, not only unit tests, and reported problems it hit along the way.

---

## Session 1: Scheduler (tasks 1–4)

### What the user provided

The opening prompt (verbatim, in part):

> "I'm working on an interview take home task as detailed in @README.md . I have already started to note down my thinking around the problem in @NOTES.md - specifically note the framing as a seqential decision problem. I first want to plan a solution to tasks 1-4 listed in the README - implementing Scheduler. Do not make any edits yet or create a plan - First explore the code then I want to discuss before we draft a plan."

The user then listed six areas to settle *(paraphrase)*:

1. Propagating the workflow DAG with Kahn's algorithm, using predecessor counters and a ready set, in a `WorkflowDAGState` class.
2. Handling instruments that are unavailable when a step becomes ready.
3. A simple rules-based dispatch (the "PFA" class in Warren Powell's taxonomy), using list scheduling by longest remaining path.
4. Processing events serially, borrowing the "decision event after the triggering events" pattern from the user's discrete-event simulation experience.
5. Letting a finished step trigger downstream work on other idle instruments.
6. Handling failures minimally for now: no hanging, and the run recorded as failed.

The prompt ended: "Thoughts on all this? Questions for me before we plan? Before planning, I will also give you instructions for coding style".

### What Claude did

*(Paraphrase.)*

- Read 14 files and reported what the code implies. For example, the concurrency test calls `handle_result` directly, so serialisation has to live inside `Scheduler` and not in the bus.
- Agreed with the counter-based Kahn approach. It noted that a topological order is only needed once, at run start, to compute ranks.
- Showed on the PCR workflow that rank-based dispatch gives a 10s makespan, against 12s for the wrong order.
- Disagreed with the user on one point: it argued retries don't need priority updates, because a step's rank doesn't change.
- Raised the choice between in-memory state and the database as the source of truth. It recommended in-memory and suggested documenting the single-executor assumption.
- Pointed out that the scheduling decision takes microseconds, while the NATS and Postgres calls dominate latency.

### Where the user pushed back or redirected

1. **Cross-run priority.** Claude had written that ranks stay independent of other runs. The user challenged this:

   > "In an earlier output you said "Ranks themselves stay independent of other runs". I want to clarify your meaning of that because I do not think it's true. The scheduler must prioritise steps over all active runs, which can be on different workflows"

   Claude separated the two meanings. The rank value is computed per run, but the priority is decided across runs. It then explained that raw rank across runs optimises batch makespan and can starve short runs, so the right rule depends on the performance target.

2. **The user's own dispatch rule.** The user assumed the target was mean flow time and proposed a lexicographic ordering. In the user's words:

   > "1. bool is step internal slack > 0 (see 3, this is to priotize steps on critical path) 2. Job remaining tail (remaining time on critical path). Lowest is high priotiy so long runs don't delay everthing else 3. Step internal slack = job remaining tail - step tail … 4. Run created time. Earliest first to finish earlier runs rather than interleaving identical ones 5. step ID for determiniscm"

   Claude turned this into a sort key and checked it against PCR. It noted that within one run the first three criteria reduce to "largest tail first", so no heap needs re-keying. It also raised refinements: the slack threshold should be "slack ≥ device duration" rather than "> 0", the rule can starve long runs, and slack ignores device contention. The user accepted these and had them recorded as known limits.

3. **Step durations.** Claude offered a hop count or durations learned from timestamps. The user rejected both, saying durations are config set in docker-compose and should be respected *(paraphrase)*. The user then chose, from Claude's options, to have each driver report its own duration.

4. **Claude's simplification.** After the user enabled the `ponytail` minimal-code plugin, Claude proposed putting everything in one `scheduler.py` with no DAG class. The user asked:

   > "…what is the argument for not having a separate RunDAGState or similar?"

   Claude replied that there was no good argument against it. It had confused "fewer files" with "fewer abstractions", and a pure class with no I/O is a natural boundary.

5. **Style instructions** (verbatim):

   > "Gngerally follow similar coding patterns to existing codebase. Keep simple where we can and avoid over-engineering, emphasise readability, and avoid using overly short variable names that do not tell a reader clearly what things are. We should not have to rely on lots of comments in the code. Keep things modularized."

The user also pressed Enter early while typing the dispatch rule, interrupted, and continued in a second message.

### Plan, implementation and verification

- Claude drafted a plan and the user approved it. It covered a pure `RunDAGState` module, a scheduler that makes decisions synchronously and sends commands concurrently, two small store guards, driver-reported durations, and one database-free test.
- Claude then implemented it and ran the live stack: PCR and Triple Assay acceptance, the failure check, the dockerised tests, two runs started back to back, and an executor restart to force a refusal.
- Reported results: PCR 5/5 in 10.05s with 0 refusals, with `fill_reagent_plate` dispatched before `fill_buffer_plate`; the failure check passing; 2 tests passing.
- Problems Claude reported:
  - `docker compose up --build` reused a stale image, so an explicit build was needed.
  - A spurious error caused by its own zsh `echo ===` separator.
  - A run left orphaned after the restart test, because state is not recovered.
  - The fixed 1s retry delay added about 1s to the refusal case.

---

## Session 2: Dropped results

### What the user provided

The user started from a handover document (`HANDOVER.md`) and asked for a failing test before any solution. Verbatim, in part:

> "First, before starting on a solution let's create a test to catch this failing behaviour. With the current implementation it would just hang. So the test should wait for a short time before killing the process and failing the test. We can force the droped results by setting INC_DROP_PCT to 100 fot the incumber to drop all steps."

The user also proposed the expected behaviour *(paraphrase)*: if nothing is heard after a set fraction beyond the expected duration, the driver is idle, and the step is in its executed list, assume the step completed. The case of a failure and a drop together was set aside for later. The pasted prompt contains a stray link fragment, which is omitted here.

### What Claude did

- Wrote `scripts/check-drop.sh`, following the pattern of `check-failure.sh`, and confirmed it fails on the current code: after 30s the run is still running with `incubate_samples` stuck at dispatched.
- Added guards so the check can't pass vacuously. It requires the incubator's dropped counter to rise, and it refuses to run if any driver has failures switched on.

### Where the user pushed back or redirected

1. **Test hygiene.** Claude said the stuck claim would persist until the executor restarted, and told the user to restart it manually. The user rejected this:

   > "Yeh this doesn't feel right to me. Tests shouldn't leave us in fragmented state. The test should clean up after itself"

   Claude changed the script to restart the executor and wait for `/health` on exit, then ran the acceptance suite straight afterwards to show the stack was usable. It stated the remaining limits: an orphaned run row stays in the database, and the restart happens even on a pass.

2. **Constant semantics and naming.** The user proposed a `STEP_X_FACTOR` of 0.5 and asked for comments. Claude asked clarifying questions, and the user corrected the meaning and the name:

   > "Multiplier: duration x 1.5 (so constant should be 1.5 not 0.5). And n reflection it's probably bad name. Perhaps `step_max_wait_multiplier` is clearer"

   The code uses `STEP_MAX_WAIT_MULTIPLIER = 1.5`.

3. **Busy but late.** Claude recommended failing the run if the driver is still busy when the deadline passes, and the user chose that option. Claude noted that a bounded re-arm of the deadline would be the fix for instruments with variable timing.

4. **Reasoning before editing.** The user wrote: "Now don't make further edits yet, but let's reason about what would happen when we have a failed run, where some other in-flight step gets dropped." Claude traced the code path *(paraphrase)* and concluded that the deadline timer still fires and the device is still freed. It flagged one remaining issue: successors of a dropped step can be marked ready on an already-failed run. That would only matter if later code reads those ready sets.

5. **Dropped failures.** The user proposed a heuristic of comparing the driver's failed counter before dispatch with its value at reconcile time. Claude wrote the failing test first. It reproduced the bug: with every incubator step failing and every result dropped, the run ended `completed`. Claude then made one change to the user's proposal. It reads the baseline just after the driver accepts the step rather than before sending, to avoid an extra round trip on the critical path. Its argument was that the driver cannot count a failure for the step until the step's duration has passed. After the change all checks passed.

### Limits Claude listed

- The heuristic assumes a single executor.
- If the real result arrives just before the reconcile query, the real result is ignored. The counter-based decision should match it.
- Synthesised failures lose the `retryable` flag.
- Assumed completions are only logged, so the database can't tell them from real ones.

### Other notes

- The user switched between Sonnet and Opus mid-session (`/model sonnet`, `/model opus`).
- Nothing from this session had been committed at the time of the export.

---

## Token usage and cost

The user supplied these totals. They are not in the exports.

| Session | Total tokens |
|---|---|
| 1 | 102.7k |
| 2 | 123k |
| **Combined** | **about 225.7k** |

**Cost: not estimated.** A cost estimate needs the split between input, cache and output tokens, and the model used for each. Only combined totals were supplied, and session 2 used two different models. The `/cost` output from each session would give the actual dollar figure.
