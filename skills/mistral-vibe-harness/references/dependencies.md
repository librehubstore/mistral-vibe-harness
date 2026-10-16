# Dependency problems: diagnose, then ask

Install fails, the resolver reports a conflict, an import breaks after an upgrade.
Budget: **2 failed install/resolve attempts** on the same problem, then stop and report.
The harness hooks enforce it: escape flags are blocked, and so are dependency changes
until the user has answered.

## 1. Read the whole error

Read the full output, not the last line. Write down:
- the package that fails and the version requested;
- the constraint it conflicts with, and who declares it (the project, or another package);
- the kind of failure: resolution conflict, no binary for this platform/Python/Node,
  build failure (compiler, Rust, node-gyp), network/TLS, permissions.

## 2. Confirm with the package manager, not memory

| Question | npm / pnpm / yarn | pip / uv |
|---|---|---|
| What is installed? | `npm ls <pkg>` | `pip show <pkg>`, `pip list` |
| What does it require? | `npm view <pkg>@<ver> peerDependencies dependencies` | `pip download <pkg>==<ver> --no-deps -d <scratch>` then read the METADATA, or `uv pip compile` |
| Which versions exist? | `npm view <pkg> versions --json` | `pip index versions <pkg>` |
| Is the env consistent? | `npm ls --all` (look for `invalid`, `UNMET`) | `pip check` |
| Runtime version | `node -v` | `python -V` |

## 3. Classify and stop at the right place

| Cause | What you do |
|---|---|
| Two declared versions conflict | Report both constraints and the 1-2 versions that satisfy them; recommend one. Ask before editing the manifest. |
| No binary for this runtime | Report the runtime and the first version that ships a binary for it. Ask: change the package version, or the runtime? |
| Build toolchain missing (compiler, Rust) | Report it. Do not install toolchains on your own. Ask. |
| TLS / proxy / network | Quote the error. Never disable verification or change the index. Ask. |
| A test or import breaks after an upgrade | Read the package's changelog or source in `node_modules` / `site-packages` for the changed API before touching code. |

## 4. Never

`--force`, `--legacy-peer-deps`, `--break-system-packages`, `--extra-index-url` / `--index-url`,
`--trusted-host`, disabling TLS, deleting a lockfile or `node_modules` to retry clean,
upgrading a major version, switching Node/Python version. All of them hide the problem.

## 5. The report

```
Dependency problem: <one line>
Error (quoted): <the decisive lines>
Cause: <constraint A> vs <constraint B> | no binary for <runtime> | ...
Checked with: <commands run>
Options:
  1. <recommended> — <what changes, risk>
  2. <alternative> — <what changes, risk>
Which one?
```
