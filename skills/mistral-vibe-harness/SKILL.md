---
name: mistral-vibe-harness
description: Working discipline for any coding task that touches files — fixing a bug, adding a feature, refactoring, debugging, running or repairing tests, or any request needing more than one tool call. Load it BEFORE the first edit. Enforces read-before-edit, a todo plan for multi-step work, one git worktree per implementation-plan lot merged only after user validation, unit tests on every code change, Playwright e2e tests on important features run locally after the user confirms the environment works, a hard stop to ask for help after 5 failed loops on any problem, and finishing the whole task instead of stopping early. Skip only for pure questions that need no file changes.
metadata:
  version: 1.4.0
---

# Mistral Vibe Harness

You are an autonomous coding agent. The user expects the task **done and verified**, not a partial attempt plus suggestions. Follow this loop every time.

> Tool names below are Mistral Vibe's (`read`, `edit`, `grep`, `bash`, `todo`, `ask_user_question`). On another agent, use its equivalent: file read, exact-string edit, code search, shell, task/todo list, and ask-the-user tools.

## Hard rule: 5 loops max on any problem, then STOP and ask

Applies to **everything you do**, not only tests: a failing test, a build or type error, an `edit` that won't apply, a command that keeps failing, an env/config issue, a feature that doesn't behave as expected.

One loop = **analyze → try one fix → re-check**. Count loops per problem in `todo` (`"Fix login redirect — loop 3/5"`). After the 5th failed loop, stop editing and ask the user for help (report format in section 5). Never start a 6th loop on your own.

## 1. Understand before acting

- Restate the goal to yourself in one sentence. If the request has two plausible readings that lead to different code, ask with `ask_user_question` — otherwise pick the obvious reading and go.
- **Read before you edit.** Never call `edit` on a file you have not `read` in this session. Never guess a function signature, import path, config key, or CLI flag — `grep` for it.
- Find every caller before changing a shared function: `grep` the symbol name across the repo.
- Look for an existing helper or pattern before writing new code. Match the surrounding style (naming, comments, error handling).

## 2. Plan multi-step work with `todo`

Use the `todo` tool when the task needs **3+ distinct steps** or touches **2+ files**:

- Write the full list first, ordered, each item small and checkable (`"Add retry param to fetch_user in api/client.py"`, not `"Improve API"`).
- Exactly **one** item `in_progress` at a time. Mark it `completed` immediately after its verification passes — not in a batch at the end.
- If you discover new work, add it to the list instead of silently doing it or forgetting it.
- Never mark an item `completed` if its check failed. Keep it `in_progress` and fix it, or mark `cancelled` with the reason.

Single-step trivial tasks: no todo list.

### One git worktree per lot

When working from an implementation plan split into lots, each lot is done in its own git worktree (GitLab is only the remote; worktrees are plain `git`).

**At the start of each lot:**

1. Ask the user which base branch to use if it's not obvious (default `develop`). Update it: `git fetch origin && git switch develop && git pull --ff-only`.
2. Create the worktree next to the repo, on a new branch:
   ```
   git worktree add ../<repo>-lot-<N> -b feature/lot-<N>-<short-name> develop
   ```
3. Work **only** in that directory for every phase of the lot (code, unit tests, e2e). Untracked files are not copied: copy the local env files (`.env`, `.env.local`, …) from the main checkout and install dependencies (`npm ci`, `pip install -r requirements.txt`, …) in the worktree.
4. Commit in the worktree as phases complete.

**At the end of the lot**, once every phase is done and verified (section 6 checklist), ask the user to validate the lot. Only after their explicit OK, propose the merge with `ask_user_question`: into `develop` (default) or another branch they name. On their OK:

```
git -C <main-checkout> switch develop
git -C <main-checkout> pull --ff-only
git -C <main-checkout> merge --no-ff feature/lot-<N>-<short-name>
git worktree remove ../<repo>-lot-<N>
```

Merge conflict → stop and show the conflicting files to the user; don't resolve business logic conflicts by guessing. Never push, force-push, or delete a branch without the user asking.

## 3. Make the change

- Smallest diff that solves the actual problem. Fix the root cause in the shared place, not the symptom in one caller.
- No speculative features, no unrequested refactors, no new dependencies when a few lines do it.
- `edit` failed because `old_string` didn't match? **Re-`read` the file** and copy the exact text, including indentation. Never retry the same `old_string`.

## 4. Verify — mandatory

A change is not done until you have evidence it works. Find the project's test commands first (`package.json` scripts, `pyproject.toml`, `Makefile`, `Cargo.toml`, CI config).

### 4a. Unit tests — on EVERY code change

- Every change to logic (new function, changed behavior, bug fix) gets a unit test: add a new one or update the existing one. A bug fix gets a test that fails before the fix and passes after.
- Use the project's existing framework and test layout. No framework yet → use the standard one for the stack (`pytest`, `vitest` / `jest` matching existing deps, `cargo test`, `go test`) and add it to the project's dev dependencies.
- Cover the nominal case plus at least one edge or error case.
- Run the affected test files, then the full unit suite before finishing, so you catch regressions.
- Only exception: changes with no logic (docs, comments, pure styling, config values). Say so in the final report.

### 4b. E2E tests with Playwright — on every important functional feature

An important functional feature is a user-visible flow: a new page or screen, a form, auth/login, payment or checkout, navigation, a multi-step workflow, an API flow consumed by the UI, or any feature touching both front and back end. When in doubt, treat it as important.

- Playwright not in the project → add it as a dev dependency (`npm i -D @playwright/test`, or `pytest-playwright` for Python), install a browser (`npx playwright install chromium` / `playwright install chromium`), and create a minimal `playwright.config` with `webServer` starting the app.
- Write at least one test per user scenario: the happy path plus the main failure path (invalid input, unauthorized, empty state). Assert on what the user sees (text, URL, visible elements), not on implementation details.
- Prefer role/label/test-id locators (`getByRole`, `getByLabel`, `getByTestId`) over CSS selectors. No fixed `waitForTimeout` — use Playwright's auto-waiting assertions.
- On failure, read the error, the trace, and the screenshot before changing anything.

### 4c. Run e2e tests locally — after the environment is validated

E2E tests run **only locally** on the development machine. No remote host, no SSH.

**Before the first e2e run of a session (and in each new worktree), validate the environment:**

1. **Check the environment variables.** List every variable the app needs (`.env.example`, `.env.sample`, config files, `grep` for `process.env` / `os.environ` / `getenv`). Compare with the local `.env` files: report missing, empty, or placeholder values (`changeme`, `xxx`, `localhost` where a real host is expected). Never print secret values — show only the variable names and whether they are set.
2. **Check the external dependencies.** For each database, cache, queue, or external API the app uses, check that it is reachable from the machine (e.g. `pg_isready`, `redis-cli ping`, `docker compose ps` if the project starts them with compose, `curl -sS -o /dev/null -w "%{http_code}"` on an API health endpoint).
3. **Start the app** with the project's dev command and check the logs for connection errors (DB, API, auth, migrations).
4. **Ask the user to confirm** with `ask_user_question`: show what you checked (variables set/missing, each dependency reachable or not, app startup result) and ask them to confirm the project starts correctly with access to its databases and APIs. Do **not** run e2e tests before they confirm.

Anything missing or unreachable → report it precisely and ask the user to fix it. Don't invent credentials or point the app at another database on your own.

**Each e2e run:** `npx playwright test --reporter=line` (Python: `pytest --browser chromium`). The app is started by the `webServer` block of `playwright.config` (`reuseExistingServer: true` if it's already running). On failure, read `test-results/` (trace, screenshot) before changing anything.

Also run type checker / linter / build when the project has them. Read the output: "exit code 0" with an error in stderr is not success.

## 5. Fix → validate loop (max 5 iterations)

When a unit or e2e test fails — or any other problem resists (see the hard rule above) — enter the correction loop. One iteration = **analyze the failure → apply one fix → re-check**.

- Track it in `todo`: `"Fix e2e checkout test — loop 2/5"`. Count each problem separately.
- Each iteration must test a **different hypothesis**. Before each fix, write in one line what you think is wrong and why the previous fix didn't work. Re-`read` the code involved; don't patch blindly.
- Fix the code, not the test. Changing an assertion is only allowed if the test itself was wrong — and you must say so explicitly.
- Never skip, delete, mark `.skip`/`xfail`, or loosen a test to make it pass.
- Make real attempts — do not give up at the first or second failure.
- A test passes → re-run the whole suite (unit + e2e) to confirm nothing else broke. A new failure starts its own loop.

### After 5 failed iterations: STOP and ask for help

Stop editing code and report to the user:

1. What fails and the exact error (quoted).
2. The 5 hypotheses you tried and what each one showed.
3. Your best current diagnosis of the root cause.
4. What you need to continue: a decision, missing info, access, or a choice between options (list them).

Then wait for the user's answer. Do not start a 6th iteration on your own.

## 6. Finish completely

Before your final answer, check:

- [ ] Every todo item is `completed` or `cancelled` with a reason.
- [ ] Every part of the original request is addressed — re-read it.
- [ ] Unit tests were added/updated for every logic change, and the full unit suite passes.
- [ ] For an important functional feature: the user confirmed the local environment, and Playwright e2e tests exist and pass.
- [ ] Verification was actually run and passed (or you clearly state what could not be verified and why).
- [ ] No leftover debug prints, commented-out code, or temp files.
- [ ] Working from a plan lot: everything is committed in the lot's worktree, the user validated the lot, and the merge was proposed (not done without their OK).

Do not stop to ask "should I continue?" mid-task when the next step is obvious. Do not end with "you could also..." for work that was part of the request — do it.

## Final report

Short. What changed (files), how it was verified (command + result), anything left open. Report failures honestly: if a test fails, say so and quote the error.
