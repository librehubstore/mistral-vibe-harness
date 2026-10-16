# Local e2e tests with Playwright

E2E tests run **only locally** on the development machine. No remote host, no SSH.

## Important functional feature = e2e test

A user-visible flow: a new page or screen, a form, auth/login, payment or checkout,
navigation, a multi-step workflow, an API flow consumed by the UI, anything touching both
front and back end. When in doubt, treat it as important.

## Set up Playwright (once per project)

Adding Playwright is a dependency change: ask the user first (the hook blocks it otherwise).
Then: `npm i -D @playwright/test` (Python: `pytest-playwright`), `npx playwright install chromium`
(Python: `playwright install chromium`), and a minimal `playwright.config` whose `webServer`
starts the app (`reuseExistingServer: true`).

## Validate the environment before the first run (and in each new worktree)

1. **Environment variables.** List what the app needs (`.env.example`, config files, `grep` for
   `process.env` / `os.environ` / `getenv`). Compare with the local `.env` files. Report missing,
   empty or placeholder values (`changeme`, `xxx`) by **name only**, never by value.
2. **External services.** For each database, cache, queue or external API, check it is reachable:
   `pg_isready`, `redis-cli ping`, `docker compose ps`, `curl -sS -o /dev/null -w "%{http_code}" <health url>`.
3. **Start the app** with the project's dev command; read the logs for connection, auth or
   migration errors.
4. **Ask the user to confirm** that the project starts correctly with access to its databases
   and APIs. Show what you checked (variables set/missing, services reachable or not, startup
   result). Do not run e2e tests before they confirm.

Missing or unreachable → report precisely and ask. Never invent credentials, never point the
app at another database on your own.

## Write the tests

- One test per user scenario: the happy path plus the main failure path (invalid input,
  unauthorized, empty state).
- Assert on what the user sees (text, URL, visible elements), not on implementation details.
- Locators: `getByRole`, `getByLabel`, `getByTestId` over CSS selectors.
- No fixed `waitForTimeout`; use Playwright's auto-waiting assertions.

## Run

`npx playwright test --reporter=line` (Python: `pytest --browser chromium`).
On failure, read the error, the trace and the screenshot in `test-results/` before changing anything.
