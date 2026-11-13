# .agents/plans — per-task implementation plans

auto-coder writes one plan per **complex task** before coding (rule: `.factory/skills/auto-coder/SKILL.md` Implement step 4; spec: `docs/specs/AI_NATIVE_SDLC_SPEC.md` SD6.4). Plans are committed with the task and kept as an audit record. Kept out of `docs/` on purpose (no MOC registration).

File name: `<task-id>.md` (e.g. `SD3.5.md`). Format, aligned with `.agents/notes/`:

```markdown
# Plan: [<task-id>] <title>

Status: planned | implemented | deviated

## Files
Files to create / modify, in order.

## Order
Implementation steps and why this order.

## Risks
What could break (contracts, stored data, existing test semantics) and how it is contained.

## Verification
How correctness will be proven: tests to add or run, commands, expected counts.

## Deviations
(Filled in when the implementation departs from the plan; updated in the same commit.)
```
