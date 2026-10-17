# One git worktree per lot

When working from an implementation plan split into lots, each lot is done in its own git
worktree (GitLab or GitHub is only the remote; worktrees are plain `git`).

## Start of a lot

1. Base branch: `develop` by default; ask if the repo has none or it is not obvious.
   Update it: `git fetch origin && git switch develop && git pull --ff-only`.
2. Create the worktree next to the repo, on a new branch:
   ```
   git worktree add ../<repo>-lot-<N> -b feature/lot-<N>-<short-name> develop
   ```
3. Work **only** in that directory for every phase of the lot. Untracked files are not copied:
   copy the local env files (`.env`, `.env.local`, ...) from the main checkout, and install
   dependencies (`npm ci`, `pip install -r requirements.txt`, ...) in the worktree.
4. Commit in the worktree as phases complete.

## End of a lot

Once every phase is done and verified (SKILL.md finish checklist), ask the user to validate
the lot. Only after their explicit OK, propose the merge: into `develop` or another branch
they name. On their OK:

```
git -C <main-checkout> switch develop
git -C <main-checkout> pull --ff-only
git -C <main-checkout> merge --no-ff feature/lot-<N>-<short-name>
git worktree remove ../<repo>-lot-<N>
```

`git worktree remove` refuses while untracked files remain (build output, `__pycache__`):
look at them before deleting anything. Merge conflict → stop and show the conflicting files;
never resolve business logic conflicts by guessing. Never push, force-push or delete a
branch without the user asking.
