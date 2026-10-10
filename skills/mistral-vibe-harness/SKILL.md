---
name: mistral-vibe-harness
description: Working discipline for any coding task that touches files — fixing a bug, adding a feature, refactoring, debugging, running or repairing tests, or any request needing more than one tool call. Load it BEFORE the first edit. Enforces read-before-edit, a todo plan for multi-step work, unit tests on every code change, Playwright e2e tests on important features run in Docker on a remote server over SSH, a 5-iteration fix/validate loop, and finishing the whole task instead of stopping early. Skip only for pure questions that need no file changes.
metadata:
  version: 1.3.0
---

# Mistral Vibe Harness

You are an autonomous coding agent. The user expects the task **done and verified**, not a partial attempt plus suggestions. Follow this loop every time.

> Tool names below are Mistral Vibe's (`read`, `edit`, `grep`, `bash`, `todo`, `ask_user_question`). On another agent, use its equivalent: file read, exact-string edit, code search, shell, task/todo list, and ask-the-user tools.

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

- Playwright not in the project → add it as a dev dependency (`npm i -D @playwright/test`, or `pytest-playwright` for Python) and create a minimal `playwright.config` with `webServer` starting the app. No local browser install needed: browsers come with the Docker image (see 4c).
- Write at least one test per user scenario: the happy path plus the main failure path (invalid input, unauthorized, empty state). Assert on what the user sees (text, URL, visible elements), not on implementation details.
- Prefer role/label/test-id locators (`getByRole`, `getByLabel`, `getByTestId`) over CSS selectors. No fixed `waitForTimeout` — use Playwright's auto-waiting assertions.
- On failure, read the error, the trace, and the screenshot before changing anything.

### 4c. Run e2e tests on the remote Docker host over SSH

E2E tests do NOT run locally. They run in Docker on the user's remote server, over SSH.

**Connection config — `.e2e-ssh.env` at the project root:**

```
E2E_SSH_HOST=203.0.113.10
E2E_SSH_USER=deploy
E2E_SSH_PORT=22
E2E_SSH_KEY=~/.ssh/id_ed25519
E2E_REMOTE_DIR=~/vibe-e2e/<project-name>
```

- File missing → ask the user with `ask_user_question` (use the free-text "Other" answer) for: host, user, port (default 22), path to the private key. Write the file, and add `.e2e-ssh.env` to `.gitignore`.
- **Key authentication only.** Never ask for, accept, write, or pass a password or passphrase. Your shell is non-interactive, so a password prompt would hang. If the user only has password access, stop and tell them to run `ssh-copy-id -i <key>.pub -p <port> <user>@<host>` themselves (on Windows: append the `.pub` content to `~/.ssh/authorized_keys` on the server), then continue.
- Never print the private key content. Never commit the env file.

**Check the connection** before the first run (always pass `-o BatchMode=yes` so it fails instead of prompting):

```
ssh -o BatchMode=yes -o ConnectTimeout=10 -i <KEY> -p <PORT> <USER>@<HOST> "docker info --format '{{.ServerVersion}}'"
```

Fails → report the exact error (unknown host, permission denied, docker not found / permission denied on docker.sock) and ask the user to fix it. Infrastructure failures do NOT count as fix/validate loops.

**Each e2e run:**

1. Sync the code (`tar` and `ssh` are built into Windows 10+, Linux and macOS):
   ```
   ssh <opts> <USER>@<HOST> "mkdir -p <REMOTE_DIR>"
   tar -czf - --exclude=node_modules --exclude=.git --exclude=test-results --exclude=playwright-report --exclude=.e2e-ssh.env . | ssh <opts> <USER>@<HOST> "tar -xzf - -C <REMOTE_DIR>"
   ```
2. Run Playwright in the official image, with the **same version** as the project's `@playwright/test` (or `playwright` Python package):
   ```
   ssh <opts> <USER>@<HOST> "cd <REMOTE_DIR> && docker run --rm --ipc=host -v \$PWD:/work -w /work mcr.microsoft.com/playwright:v<VERSION>-noble sh -c 'npm ci && npx playwright test --reporter=line'"
   ```
   Python projects: image `mcr.microsoft.com/playwright/python:v<VERSION>-noble`, command `pip install -r requirements.txt && pytest --browser chromium`.
   The app is started inside the container by the `webServer` block of `playwright.config`. If the app needs other services (DB, cache), use the project's `docker-compose` file on the remote: `docker compose up -d --build`, run Playwright on the same compose network, then `docker compose down` at the end.
3. On failure, fetch the artifacts and analyze them locally:
   ```
   scp -r -P <PORT> -i <KEY> <USER>@<HOST>:<REMOTE_DIR>/test-results ./test-results
   ```

Each fix/validate iteration = local fix → sync → remote run. Containers use `--rm`, so nothing is left running. Never run other docker commands on the server (no `docker rm`, `prune`, or touching containers you didn't start).

Also run type checker / linter / build when the project has them. Read the output: "exit code 0" with an error in stderr is not success.

## 5. Fix → validate loop (max 5 iterations)

When a unit or e2e test fails, enter the correction loop. One iteration = **analyze the failure → apply one fix → re-run the failing tests**.

- Track it in `todo`: `"Fix e2e checkout test — loop 2/5"`. Count unit and e2e loops separately.
- Each iteration must test a **different hypothesis**. Before each fix, write in one line what you think is wrong and why the previous fix didn't work. Re-`read` the code involved; don't patch blindly.
- Fix the code, not the test. Changing an assertion is only allowed if the test itself was wrong — and you must say so explicitly.
- Never skip, delete, mark `.skip`/`xfail`, or loosen a test to make it pass.
- You must make the full 5 attempts before giving up — do not stop at the first or second failure.
- A test passes → re-run the whole suite (unit + e2e) to confirm nothing else broke. A new failure starts its own loop.

### After 5 failed iterations: STOP

Stop editing code and report to the user:

1. Which test fails and the exact error (quoted).
2. The 5 hypotheses you tried and what each one showed.
3. Your best current diagnosis of the root cause.
4. What you need to continue: a decision, missing info, access, or a choice between options (list them).

Then wait for the user's answer. Do not start a 6th iteration on your own.

## 6. Finish completely

Before your final answer, check:

- [ ] Every todo item is `completed` or `cancelled` with a reason.
- [ ] Every part of the original request is addressed — re-read it.
- [ ] Unit tests were added/updated for every logic change, and the full unit suite passes.
- [ ] For an important functional feature: Playwright e2e tests exist and pass.
- [ ] Verification was actually run and passed (or you clearly state what could not be verified and why).
- [ ] No leftover debug prints, commented-out code, or temp files.

Do not stop to ask "should I continue?" mid-task when the next step is obvious. Do not end with "you could also..." for work that was part of the request — do it.

## Final report

Short. What changed (files), how it was verified (command + result), anything left open. Report failures honestly: if a test fails, say so and quote the error.
