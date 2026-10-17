"""Mistral Vibe hook: blocks what the harness prompt forbids but the model still does.

One script for every hook type (see ../hooks.toml); Vibe sends the event as JSON
on stdin. A pre_tool denial becomes the tool error the model sees.

- Always blocked: dependency/TLS escape hatches, deleting lockfiles or node_modules.
- Needs the user: changing a dependency, editing .env*, a manifest's versions or a
  lockfile. Blocked inside the agent's turn; once the agent has stopped and the user
  answered (post_agent), the same action goes through.
- Destructive git (reset --hard, checkout/restore of files, clean, stash drop,
  branch -D, push) needs the user too.
- Loop budget per turn: an identical failing command twice, 2 failed installs,
  8 failed commands, then only reporting is left.
- Verification: code edited and no test/build/check command run since → the end of
  the turn is refused once with "verify, then report". A failing test command gets a
  reminder that the report must say so.

    python guard.py --selftest
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

STATE_DIR = Path(tempfile.gettempdir()) / "vibe-harness"
MAX_SAME_FAILURE = 2
MAX_FAILED_INSTALLS = 2
MAX_FAILED_COMMANDS = 8  # ponytail: one cap per turn, not per problem; split by problem if it bites
MAX_VERIFY_ASKS = 2  # Vibe allows 3 post_agent retries per turn; keep one for the subset rule

ESCAPES = [
    (r"--force(?![-\w])", "--force"),
    (r"--legacy-peer-deps", "--legacy-peer-deps"),
    (r"--break-system-packages", "--break-system-packages"),
    (r"--extra-index-url|--index-url|(^|\s)-i\s+https?://", "a different package index"),
    (r"--trusted-host|--no-verify|NODE_TLS_REJECT_UNAUTHORIZED|strict-ssl\W+false|sslverify\W+false|--insecure",
     "disabling TLS verification"),
    (r"\b(rm|del|rmdir|rd|Remove-Item)\b[^|;&]*(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock|node_modules)",
     "deleting a lockfile or node_modules"),
]
INSTALL = re.compile(
    r"\b(npm|pnpm|yarn)\s+(i|install|ci|add|up|upgrade|update)\b|\bpip3?\s+install\b"
    r"|\buv\s+(add|sync|pip\s+install|lock)\b|\bpoetry\s+(add|install|update|lock)\b", re.I)
PKG_CMD = re.compile(r"\b(npm|pnpm|yarn)\s+(i|install|add|up|upgrade|update|remove|uninstall|rm)\b(.*)"
                     r"|\bpip3?\s+install\b(.*)", re.I)
DEP_CHANGE = re.compile(r"\bpip3?\s+uninstall\b|\buv\s+(add|remove)\b|\bpoetry\s+(add|remove|update)\b", re.I)
PIP_FILE_FLAGS = {"-r", "--requirement", "-e", "--editable", "-c", "--constraint"}


def changes_dependency(cmd):
    """True for commands that pick a package or a version, not a plain install of the manifest."""
    if DEP_CHANGE.search(cmd):
        return True
    for segment in re.split(r"&&|\|\||;|\|", cmd):
        m = PKG_CMD.search(segment)
        if not m:
            continue
        tokens, skip = (m.group(3) if m.group(1) else m.group(4)).split(), False
        for tok in tokens:
            if skip:
                skip = False
            elif tok in PIP_FILE_FLAGS:
                skip = True
            elif not tok.startswith("-") and tok != "." and not tok.startswith(("2>", ">")):
                return True
    return False


DESTRUCTIVE_GIT = re.compile(
    r"\bgit\s+(reset\s+.*--hard|reset\s+--hard|clean\s+-\w*f|checkout\s+(--\s|\.(\s|$)|-f\b)|restore\s+(?!--staged)"
    r"|stash\s+(drop|clear)|branch\s+-D|push\b)", re.I)
CHECK_CMD = re.compile(
    r"\b(pytest|unittest|nose2|tox|npm\s+(run\s+)?(test|build|lint|check|typecheck)|pnpm\s+(run\s+)?(test|build)"
    r"|yarn\s+(test|build)|vitest|jest|playwright\s+test|tsc\b|mypy|ruff|eslint|go\s+(test|build|vet)"
    r"|cargo\s+(test|build|check)|mvn\s+(test|verify)|gradle\w*\s+(test|build)|dotnet\s+(test|build)|make\s+(test|check))",
    re.I)
CODE_FILE = re.compile(
    r"\.(py|js|mjs|cjs|ts|tsx|jsx|vue|svelte|go|rs|java|kt|rb|php|cs|c|cc|cpp|h|hpp|swift|scala|sql|sh|ps1)$", re.I)
VERIFY = ("BLOCKED by harness: you changed code ({files}) and ran no test, build or check command since. "
          "Run the relevant tests (or the project's build/check) now and report the actual result. "
          "If the project has no way to check this change, say so explicitly in your report.")
# A check narrowed to a file, a test id or a filter: a subset of the suite.
TARGETED = re.compile(
    r"\bunittest\b(\s+-\w+)*\s+(?!discover\b)(?![-&|;>\d])\S"
    r"|\bpytest\b[^|;&]*(\s-k\s|::|\s[\w./\\-]+\.py\b|\s(tests?|src)[/\\]\S+)"
    r"|\b(vitest|jest)\b[^|;&]*\s[\w./\\-]+\.(test|spec)\.\w+|--testNamePattern", re.I)
SUBSET = ("BLOCKED by harness: since your last edit you only ran part of the test suite (`{cmd}`). "
          "Run the project's full test suite once, then report its actual result, including failures "
          "unrelated to your change.")
TEST_FAILED = ("harness: this check failed. Your final report must say so and quote the decisive error; "
               "never claim the tests pass.")
ENV_FILE = re.compile(r"^\.env(\..+)?$")
ENV_TEMPLATE = re.compile(r"\.(example|sample|template|dist)$")
LOCKFILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "pipfile.lock"}
VERSION_SPEC = re.compile(r"""["']?[\w@/.-]+["']?\s*(:|==|>=|<=|~=|!=|=)\s*["']?[\^~<>=]*\d+\.\d+""")

ASK = " Stop here: explain the situation to the user and ask what to do. Do not look for another way around it."


def blocked(why):
    return {"decision": "deny", "reason": f"BLOCKED by harness: {why}.{ASK}"}


def normalize(cmd):
    cmd = re.sub(r"^\s*(cd\s+\S+\s*(&&|;)\s*)+", "", cmd)  # same command from another cwd is the same command
    return " ".join(cmd.split())


def load(session_id):
    try:
        return json.loads((STATE_DIR / f"{session_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"failures": {}, "failed_installs": 0, "failed_commands": 0, "pending": [], "approved": []}


def save(session_id, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{session_id}.json").write_text(json.dumps(state), encoding="utf-8")


def needs_user(state, key, why):
    """Deny until the user has had a turn since the first attempt."""
    if key in state["approved"]:
        return None
    if key not in state["pending"]:
        state["pending"].append(key)
    return blocked(f"{why} needs the user's decision")


def pre_bash(state, cmd):
    for pattern, label in ESCAPES:
        if re.search(pattern, cmd, re.I):
            return blocked(f"{label} is never allowed")
    norm = normalize(cmd)
    if state["failed_commands"] >= MAX_FAILED_COMMANDS:
        return blocked(f"{state['failed_commands']} commands failed in this turn, the loop budget is spent. "
                       "Report the exact errors, what you tried and your diagnosis")
    if state["failures"].get(norm, 0) >= MAX_SAME_FAILURE:
        return blocked("this exact command already failed twice; running it again changes nothing")
    if INSTALL.search(cmd) and state["failed_installs"] >= MAX_FAILED_INSTALLS:
        return blocked(f"{state['failed_installs']} install attempts failed, the dependency budget is spent. "
                       "Report the exact error and your diagnosis")
    if changes_dependency(cmd):
        return needs_user(state, "bash:" + norm, "adding, removing or changing a dependency version")
    if DESTRUCTIVE_GIT.search(cmd):
        return needs_user(state, "bash:" + norm, "a git command that discards work or publishes it")
    return None


def pre_file(state, tool, args):
    path = args.get("file_path") or args.get("path") or ""
    name = Path(path).name.lower()
    key = f"file:{os.path.normcase(os.path.abspath(path))}"
    if ENV_FILE.match(name) and not ENV_TEMPLATE.search(name):
        return needs_user(state, key, f"editing {name} (secrets and local config are the user's)")
    if name in LOCKFILES:
        return needs_user(state, key, f"editing the lockfile {name}")
    if re.match(r"requirements.*\.txt$", name):
        return needs_user(state, key, f"changing dependencies in {name}")
    if name in ("package.json", "pyproject.toml"):
        text = args.get("content", "") if tool == "write_file" else args.get("old_string", "") + "\n" + args.get("new_string", "")
        if tool == "write_file" or VERSION_SPEC.search(text):
            return needs_user(state, key, f"changing dependency versions in {name}")
    return None


def post_bash(state, event):
    out = event.get("tool_output") or {}
    code = out.get("returncode", out.get("exit_code", 0))
    cmd = event["tool_input"].get("command", "")
    is_check = bool(CHECK_CMD.search(cmd))
    if is_check:  # pass or fail, the change was checked; the report has to tell which
        if state.get("unverified") or state.get("subset_cmd"):
            # ponytail: a full run is asked even when the user wanted one test; one extra run per turn
            state["subset_cmd"] = normalize(cmd) if TARGETED.search(cmd) else None
        state["unverified"] = []
    if event.get("tool_status") == "success" and not code:
        return None
    norm = normalize(cmd)
    state["failures"][norm] = state["failures"].get(norm, 0) + 1
    state["failed_commands"] += 1
    if INSTALL.search(cmd):
        state["failed_installs"] += 1
    if is_check:
        return {"hook_specific_output": {"additional_context": TEST_FAILED}}
    return None


def post_file(state, event):
    path = event["tool_input"].get("file_path") or event["tool_input"].get("path") or ""
    if event.get("tool_status") == "success" and CODE_FILE.search(path):
        name = Path(path).name
        if name not in state.setdefault("unverified", []):
            state["unverified"].append(name)


def end_of_turn(state):
    """Refuse the end of a turn once if code changed without a check; else hand the floor to the user."""
    if state.get("unverified") and state.get("verify_asked", 0) < MAX_VERIFY_ASKS:
        state["verify_asked"] = state.get("verify_asked", 0) + 1
        again = "Second request: a one-off `python -c` or manual run is not the test suite. " if state["verify_asked"] > 1 else ""
        return {"decision": "deny", "reason": again + VERIFY.format(files=", ".join(state["unverified"][:5]))}
    if state.get("subset_cmd") and not state.get("subset_asked"):
        state["subset_asked"] = True
        return {"decision": "deny", "reason": SUBSET.format(cmd=state["subset_cmd"])}
    state.update(failures={}, failed_installs=0, failed_commands=0, verify_asked=0,
                 subset_cmd=None, subset_asked=False,
                 approved=state["approved"] + state["pending"], pending=[])
    return None


def handle(event):
    sid = event.get("session_id") or "default"
    state = load(sid)
    kind = event.get("hook_event_name")
    tool = event.get("tool_name", "").rsplit(".", 1)[-1]  # unified harness: "file_system.bash"
    if tool in ("git_bash", "powershell"):  # native Windows shells
        tool = "bash"
    result = None
    if kind == "pre_tool" and tool == "bash":
        result = pre_bash(state, event["tool_input"].get("command", ""))
    elif kind == "pre_tool" and tool in ("edit", "search_replace", "write_file"):
        result = pre_file(state, tool, event["tool_input"])
    elif kind == "post_tool" and tool == "bash":
        result = post_bash(state, event)
    elif kind == "post_tool" and tool in ("edit", "search_replace", "write_file"):
        post_file(state, event)
    elif kind == "post_agent":  # the user gets the floor: new budget, pending decisions were seen
        result = end_of_turn(state)
    save(sid, state)
    return result


def selftest():
    global STATE_DIR
    STATE_DIR = Path(tempfile.mkdtemp())

    def pre(cmd):
        return handle({"session_id": "t", "hook_event_name": "pre_tool", "tool_name": "bash",
                       "tool_input": {"command": cmd}})

    def fail(cmd):
        handle({"session_id": "t", "hook_event_name": "post_tool", "tool_name": "bash",
                "tool_input": {"command": cmd}, "tool_status": "success", "tool_output": {"returncode": 1}})

    def edit(path, old="", new=""):
        return handle({"session_id": "t", "hook_event_name": "pre_tool", "tool_name": "file_system.search_replace",
                       "tool_input": {"file_path": path, "old_string": old, "new_string": new}})

    for cmd in ("npm install --legacy-peer-deps", "pip install x --trusted-host pypi.org",
                "rm -rf node_modules package-lock.json", "git push --force", "pip install -i https://evil/simple x",
                "Remove-Item -Recurse node_modules"):
        assert pre(cmd), cmd
    for cmd in ("npm install", "npm ci", "pip install -r requirements.txt", "pip install -e .",
                "pip install --force-reinstall -r requirements.txt", "npm test"):
        assert not pre(cmd), cmd
    for cmd in ("npm install react@19", "npm i -D vitest", "pip install pydantic==2.14.1", "uv add httpx"):
        assert pre(cmd), cmd
    fail("cd /a && npm install")
    assert not pre("npm install")
    fail("cd /b && npm install")
    assert "already failed twice" in pre("npm install")["reason"]
    assert "dependency budget" in pre("npm ci")["reason"]
    assert not pre("python -m pytest")
    assert edit(".env", "", "API_KEY=x") and not edit(".env.example", "", "API_KEY=")
    assert edit("package.json", '"react": "18.3.1"', '"react": "19.0.0"')
    assert not edit("package.json", '"test": "node test.js"', '"test": "vitest"')
    assert edit("requirements-dev.txt") and edit("yarn.lock")
    handle({"session_id": "t", "hook_event_name": "post_agent"})
    assert not pre("npm install"), "budget resets once the user had a turn"
    assert not pre("npm install react@19") and not edit(".env", "", "API_KEY=x"), "asked, then allowed"
    assert pre("npm install react@18"), "only what was asked about is allowed"
    assert pre("npm install --force"), "escapes stay blocked"

    for cmd in ("git reset --hard", "git reset HEAD~1 --hard", "git checkout -- app.py", "git checkout .",
                "git restore notes.md", "git clean -fd", "git stash drop", "git branch -D old", "git push origin main",
                "git push --force-with-lease"):
        assert pre(cmd), cmd
    for cmd in ("git status", "git diff", "git stash", "git restore --staged a.py", "git checkout -b feat",
                "git commit -m x", "git log --oneline"):
        assert not pre(cmd), cmd

    def post(tool, inp, code=0):
        return handle({"session_id": "t", "hook_event_name": "post_tool", "tool_name": tool, "tool_input": inp,
                       "tool_status": "success", "tool_output": {"returncode": code}})

    def end():
        return handle({"session_id": "t", "hook_event_name": "post_agent"})

    end()  # clean turn
    post("file_system.search_replace", {"file_path": "README.md"})
    assert not end(), "docs only: no verification needed"
    post("file_system.search_replace", {"file_path": "src/app.py"})
    assert "ran no test" in end()["reason"], "code edited, nothing checked"
    assert "Second request" in end()["reason"], "asked twice"
    assert not end(), "then the turn may end"
    post("file_system.search_replace", {"file_path": "src/app.py"})
    assert "must say so" in post("bash", {"command": "python -m pytest -q"}, 1)["hook_specific_output"]["additional_context"]
    assert not end(), "a failing check still counts as verified"
    post("edit", {"file_path": "a.ts"})
    assert not post("bash", {"command": "cd x && npm test"}) and not end()

    for cmd in ("python -m unittest test_words.py -v", "python -m unittest -v tests.test_a", "pytest tests/test_a.py",
                "pytest -k slug", "pytest test_x.py::test_y", "npx vitest run src/a.test.ts"):
        assert TARGETED.search(cmd), cmd
    for cmd in ("python -m unittest", "python -m unittest -q 2>&1", "python -m unittest discover -v",
                "pytest -q", "npm test", "cd /x && python -m pytest"):
        assert not TARGETED.search(cmd), cmd
    post("edit", {"file_path": "words.py"})
    post("bash", {"command": "python -m unittest test_words.py -v"})
    assert "only ran part" in end()["reason"], "subset after an edit"
    assert not end(), "asked once"
    post("edit", {"file_path": "words.py"})
    post("bash", {"command": "python -m unittest test_words.py"})
    post("bash", {"command": "python -m unittest"}, 1)
    assert not end(), "full suite ran, even failing"
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        decision = handle(json.load(sys.stdin))
        if decision:
            print(json.dumps(decision))
