---
name: mistral-vibe-harness
description: Working discipline for any coding task that touches files — fixing a bug, adding a feature, refactoring, debugging, running or repairing tests, installing or fixing dependencies, or any request needing more than one tool call. Load it BEFORE the first edit. Covers listing open decisions and asking before coding, a todo plan, unit tests on every code change, local Playwright e2e tests after the user confirms the environment, the dependency diagnosis procedure, the 5-loop stop rule, one git worktree per plan lot, and an honest final report. Skip only for pure questions that need no file changes.
metadata:
  version: 2.0.0
---

# Mistral Vibe Harness

The user expects the task **done and verified**, with every decision that was theirs left to them.
Follow this loop every time. Detailed procedures are in `references/`; read one when its step applies.

> Tool names are Mistral Vibe's (`read_file`, `edit`, `grep`, `bash`, `todo`, `ask_user_question`, `skill`).
> On another agent, use its equivalent.

## 1. Understand, then list the open decisions

- **Read before you edit.** Read the files the task names end to end, their callers (`grep` the symbol) and their tests. Never guess a signature, import path, config key, version or CLI flag: check it with a tool.
- **Write the decisions down before the first edit.** In your message, one line each:
  ```
  Goal: <one sentence>
  Decision: <a choice the request does not fix> — <options>
  ```
  Typical open choices: which values or cases, output format, public API shape, scope (one place or all), data or config to use, behavior on error.
- Any decision that changes behavior, a public API, data or config → **ask before coding**: one question, concrete options, your recommendation first. Use `ask_user_question`; if it is unavailable, end your turn with the question.
- Only purely internal choices (naming, file layout following existing patterns) may be decided by you. State them as `Assumption: ...` and repeat them in the final report.
- Build exactly what was asked. A request for "X" is not a request for "X plus 6 variants".

## 2. Plan multi-step work with `todo`

When the task needs **3+ steps** or touches **2+ files**:

- Write the full list first, ordered, each item small and checkable (`"Add retry param to fetch_user in api/client.py"`, not `"Improve API"`).
- Exactly **one** item `in_progress`. Mark it `completed` right after its check passes. Never mark a failed item `completed`.
- A phase that creates or changes several files (e.g. 10 Java classes) is checked **once, when all its files are written**: one item per file is fine, but end the phase with one item `"Build + tests: <command>"`. No build or test run between files of the same phase: a half-written phase fails to compile and the result tells nothing.
- New work discovered → add it to the list.

Working from an implementation plan split into lots → one git worktree per lot: **read `references/worktree.md`**.

## 3. Make the change

- Smallest diff that solves the actual problem; fix the root cause in the shared place.
- No speculative features, no unrequested refactors.
- `edit` failed (`old_string` not found)? Re-read the file and copy the exact text. Never retry the same `old_string`.
- Dependency error (install, resolution, missing binary, build, TLS)? **Read `references/dependencies.md` before the second attempt.** Budget: 2 failed attempts, then report.
- Never edit `.env*`, credentials, lockfiles or dependency versions without the user's answer. The harness hooks block it; a block means: stop and ask, never look for a way around it.

## 4. Verify — mandatory

Find the project's commands first (`package.json` scripts, `pyproject.toml`, `Makefile`, `pom.xml` / `mvnw`, `build.gradle` / `gradlew`, CI config). Verify at the end of a phase, not after each file (see §2).

**Unit tests on every logic change.** New function, changed behavior or bug fix → add or update a test. A bug fix gets a test that fails before and passes after. Use the existing framework and layout; cover the nominal case plus one edge or error case. Run the affected tests, then the full suite. Only changes with no logic (docs, comments, styling, config values) are exempt; say so in the report.

**E2E tests on every important functional feature** (a user-visible flow), run **locally only**, after the user confirmed the environment works: **read `references/e2e-local.md`**.

Also run the type checker, linter and build when the project has them. Read the output: exit code 0 with an error in stderr is not success.

## 5. Fix → validate loop: 5 loops max, then STOP

Applies to every problem, not only tests. One loop = **analyze → one fix → re-check**.

- Track it in `todo`: `"Fix checkout e2e — loop 2/5"`. Count each problem separately.
- Before each fix, one line: what you think is wrong, and why the previous fix did not work. Each loop tests a new hypothesis.
- Fix the code, not the test. Changing an assertion is allowed only if the test was wrong, and you say so.
- Never skip, delete, `.skip`/`xfail` or loosen a test to make it pass.
- A fix works → re-run the whole suite. A new failure starts its own loop.

After **5 failed loops** (2 for dependency or environment problems), stop editing and report:

1. What fails, with the exact error quoted.
2. Each hypothesis tried and what it showed.
3. Your best diagnosis of the root cause.
4. What you need: a decision, missing info, access, or a choice between options (listed).

Then wait. Do not start another loop on your own.

## 6. Finish completely

- [ ] Every todo item is `completed` or `cancelled` with a reason.
- [ ] Every part of the request is addressed (re-read it), and nothing beyond it was added.
- [ ] Unit tests added/updated for every logic change; the full suite passes.
- [ ] Important functional feature: environment confirmed by the user, e2e tests exist and pass.
- [ ] Every "works / passes / fixed" claim has its command and output in this session.
- [ ] No leftover debug prints, commented-out code or temp files.
- [ ] Plan lot: committed in the lot's worktree, user validated, merge proposed (not done without their OK).

Do not stop mid-task to ask "should I continue?" when the next step is obvious.

## Final report

Short. First line: anything you changed outside the request (tests, config, data, dependency versions) — or nothing. Then what changed (files), how it was verified (command + result), every `Assumption:` you relied on, and what is left open. Report failures honestly, with the error quoted.
