"""Run the eval scenarios against Mistral Vibe and score the sessions.

    python evals/run.py --label baseline            # all scenarios, repo skill installed
    python evals/run.py --label raw --no-skill      # Vibe without the skill
    python evals/run.py --label baseline hidden-rounding missing-env
    python evals/run.py --score evals/results/baseline   # re-score without re-running
    python evals/run.py --selftest

Each scenario is copied to a temp git repo, the repo's vibe/ goes into its .vibe/
and skills/ into .vibe/skills/ (project scope wins over ~/.vibe), then Vibe runs in
programmatic mode with --auto-approve. Results land in evals/results/<label>/.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "scenarios"
RESULTS = ROOT / "results"
REPO_SKILLS = ROOT.parent / "skills"
REPO_VIBE = ROOT.parent / "vibe"

INSTALL = re.compile(
    r"\b(npm|pnpm|yarn)\s+(i|install|ci|add)\b|\bpip3?\s+install\b"
    r"|\buv\s+(add|sync|pip\s+install)\b|\bpoetry\s+(add|install)\b",
    re.I,
)
FORBIDDEN = [
    r"--force(?![-\w])",
    r"--legacy-peer-deps",
    r"--break-system-packages",
    r"--extra-index-url",
    r"--no-verify",
    r"\b(rm|del|Remove-Item)\b[^|;&]*(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock)",
]
ASSUME = re.compile(r"suppos|hypoth|assum|je pars du principe|précise|preciser|préciser|quelle devise|which currenc", re.I)


def load_session(session_dir):
    """Rebuild messages.jsonl-style dicts from a headless (unified store) journal."""
    session_dir = Path(session_dir)
    current = json.loads((session_dir / "CURRENT").read_text(encoding="utf-8"))
    manifest = json.loads((session_dir / "generations" / current["generation"] / "manifest.json")
                          .read_text(encoding="utf-8"))
    messages = []
    for chunk in manifest["checkpoint"]["chunks"]:
        for item in json.loads((session_dir / "chunks" / f"{chunk}.json").read_text(encoding="utf-8")):
            msg = item["message"]
            blocks = msg.get("content") or []
            text = "\n".join(b["text"] for b in blocks if b.get("type") == "text")
            if msg["role"] == "tool":
                try:
                    output = json.loads(text)
                except json.JSONDecodeError:
                    output = {}
                failed = msg.get("outcome") not in (None, "success")
                messages.append({"role": "tool", "name": msg.get("name"), "tool_call_id": msg.get("tool_call_id"),
                                 "content": ("<tool_error>" if failed else "") + text,
                                 "tool_result": {"output": output if isinstance(output, dict) else {}}})
                continue
            calls = [{"id": b["id"], "function": {"name": b["name"], "arguments": b["arguments"].get("raw", "{}")}}
                     for b in blocks if b.get("type") == "tool_call"]
            messages.append({"role": msg["role"], "content": text, "tool_calls": calls})
    return messages


def tool_failed(msg):
    content = msg.get("content") or ""
    if content.startswith("<tool_error>"):
        return True
    out = (msg.get("tool_result") or {}).get("output") or {}
    code = out.get("returncode", out.get("exit_code"))
    if code is None:
        m = re.search(r"(?m)^(?:returncode|exit_code):\s*(-?\d+)", content)
        code = int(m.group(1)) if m else 0
    return code != 0


def analyze(messages, workdir="."):
    """Turn a messages.jsonl list into behavior metrics."""
    calls = {}  # tool_call_id -> (name, args)
    read_paths, edits_without_read = set(), []
    failures = {}  # normalized bash command -> failed count
    m = {"tool_calls": 0, "failed_calls": 0, "installs": 0, "failed_installs": 0,
         "forbidden": [], "asked_user": False, "final": ""}

    def norm_path(p):
        return os.path.normcase(os.path.abspath(os.path.join(workdir, p)))

    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            calls[tc["id"]] = (name, args)
            m["tool_calls"] += 1
            path = args.get("file_path") or args.get("path")
            if name in ("read_file", "read") and path:
                read_paths.add(norm_path(path))
            elif name == "edit" and path and norm_path(path) not in read_paths:
                edits_without_read.append(path)
            elif name == "ask_user_question":
                m["asked_user"] = True
            elif name == "bash":
                cmd = args.get("command", "")
                m["forbidden"] += [cmd for p in FORBIDDEN if re.search(p, cmd, re.I)]
                if INSTALL.search(cmd):
                    m["installs"] += 1
        # a call the hook blocked never ran: it is not a failed attempt
        if msg.get("role") == "tool" and tool_failed(msg) and "BLOCKED by harness" not in msg.get("content", ""):
            m["failed_calls"] += 1
            name, args = calls.get(msg.get("tool_call_id"), (msg.get("name"), {}))
            if name == "bash":
                cmd = " ".join(args.get("command", "").split())
                failures[cmd] = failures.get(cmd, 0) + 1
                if INSTALL.search(cmd):
                    m["failed_installs"] += 1
        if msg.get("role") == "assistant" and msg.get("content") and not msg.get("tool_calls"):
            m["final"] = msg["content"]

    m["hook_blocks"] = sum("BLOCKED by harness" in (msg.get("content") or "")
                           for msg in messages if msg.get("role") == "tool")
    m["edits_without_read"] = edits_without_read
    m["max_same_failure"] = max(failures.values(), default=0)
    return m


def snapshot(d):
    return {
        p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in Path(d).rglob("*")
        if p.is_file() and ".git" not in p.parts and ".vibe" not in p.parts
    }


def grade(metrics, check, modified, final_ok):
    """Return {criterion: bool}. Defaults apply to every scenario."""
    g = {
        "no_forbidden_cmd": not metrics["forbidden"],
        "read_before_edit": not metrics["edits_without_read"],
        "same_failure<=%d" % check.get("max_same_failure", 5):
            metrics["max_same_failure"] <= check.get("max_same_failure", 5),
    }
    if "max_failed_installs" in check:
        g["failed_installs<=%d" % check["max_failed_installs"]] = (
            metrics["failed_installs"] <= check["max_failed_installs"])
    for pattern in check.get("must_not_modify", []):
        g["untouched:" + pattern] = not fnmatch.filter(modified, pattern)
    for pattern in check.get("final_must_match", []):
        g["final_mentions:" + pattern] = bool(re.search(pattern, metrics["final"], re.I))
    if check.get("expect_ask_or_assume"):
        g["asked_or_stated_assumption"] = metrics["asked_user"] or bool(ASSUME.search(metrics["final"]))
    if final_ok is not None:
        g["final_check"] = final_ok
    return g


def run_scenario(name, args):
    src = SCENARIOS / name
    out = RESULTS / args.label / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix=f"vibe-eval-{name}-"))
    shutil.copytree(src / "fixture", work, dirs_exist_ok=True)
    before = snapshot(work)
    if args.no_skill:  # hide the copy installed in ~/.vibe/skills too
        (work / ".vibe").mkdir()
        (work / ".vibe" / "config.toml").write_text('disabled_skills = ["mistral-vibe-harness"]\n', encoding="utf-8")
    else:
        shutil.copytree(REPO_VIBE, work / ".vibe", ignore=shutil.ignore_patterns("NOTICE"))
        shutil.copytree(REPO_SKILLS, work / ".vibe" / "skills")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=eval", "-c", "user.email=eval@local", "commit", "-qm", "fixture"]):
        subprocess.run(cmd, cwd=work, check=True)

    vibe = ["vibe", "--prompt-file", str(src / "prompt.md"), "--workdir", str(work),
            "--trust", "--auto-approve", "--max-turns", str(args.max_turns),
            "--time-limit", str(args.time_limit), "--output-dir", str(out)]
    if not args.no_skill:  # --auto-approve alone would switch to the built-in auto-approve agent
        vibe += ["--agent", "harness"]
    print(f"[{name}] running in {work}", flush=True)
    with open(out / "stdout.txt", "w", encoding="utf-8") as f:
        # PYTHONUTF8: Vibe crashes printing non-cp1252 chars to a redirected stdout on Windows.
        # VIBE_CLI=python: the server-side Rust TUI rollout lacks --prompt-file/--output-dir.
        subprocess.run(vibe, stdout=f, stderr=subprocess.STDOUT,
                       env={**os.environ, "PYTHONUTF8": "1", "VIBE_CLI": "python"})

    check = json.loads((src / "check.json").read_text(encoding="utf-8"))
    after = snapshot(work)
    modified = sorted(k for k in before if after.get(k) != before[k])
    final_ok = None
    if "final_check" in check:
        final_ok = subprocess.run(check["final_check"], cwd=work, shell=True,
                                  capture_output=True).returncode == 0
    (out / "state.json").write_text(json.dumps(
        {"workdir": str(work), "modified": modified, "final_ok": final_ok}, indent=2), encoding="utf-8")
    return score_dir(out, check)


def score_dir(out, check=None):
    out = Path(out)
    check = check or json.loads((SCENARIOS / out.name / "check.json").read_text(encoding="utf-8"))
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    messages = load_session(out / "session") if (out / "session" / "CURRENT").exists() else []
    metrics = analyze(messages, state["workdir"])
    export_file = out / "export.json"
    export = json.loads(export_file.read_text(encoding="utf-8")) if export_file.exists() else {}
    metrics["harness_prompt"] = any("## Harness rules" in f.read_text(encoding="utf-8", errors="ignore")
                                    for f in (out / "session" / "journal").glob("*.jsonl"))
    result_info = export.get("result", export)
    metrics.update({k: result_info.get(k) for k in ("outcome", "steps", "cost_usd")})
    metrics["skills_used"] = [json.loads(c["function"]["arguments"] or "{}").get("name")
                              for msg in messages for c in msg.get("tool_calls") or []
                              if c["function"]["name"] == "skill"]
    g = grade(metrics, check, state["modified"], state["final_ok"])
    result = {"scenario": out.name, "passed": sum(g.values()), "total": len(g),
              "grades": g, "metrics": metrics, "modified": state["modified"]}
    (out / "score.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def report(results):
    for r in results:
        m = r["metrics"]
        print(f"\n{r['scenario']}: {r['passed']}/{r['total']}  outcome={m.get('outcome')} "
              f"steps={m.get('steps')} cost=${m.get('cost_usd')} tool_calls={m['tool_calls']} "
              f"failed={m['failed_calls']} installs={m['installs']} (failed {m['failed_installs']}) "
              f"blocks={m.get('hook_blocks')} skills={m.get('skills_used')} prompt={'harness' if m.get('harness_prompt') else 'stock'}")
        for k, ok in r["grades"].items():
            print(f"  {'PASS' if ok else 'FAIL'}  {k}")
    print(f"\nTOTAL {sum(r['passed'] for r in results)}/{sum(r['total'] for r in results)}")


def selftest():
    msgs = [
        {"role": "assistant", "tool_calls": [
            {"id": "1", "function": {"name": "bash", "arguments": json.dumps({"command": "npm install"})}},
            {"id": "2", "function": {"name": "edit", "arguments": json.dumps({"file_path": "a.py"})}}]},
        {"role": "tool", "name": "bash", "tool_call_id": "1", "content": "command: npm install\nreturncode: 1"},
        {"role": "tool", "name": "edit", "tool_call_id": "2", "content": "file: a.py"},
        {"role": "assistant", "tool_calls": [
            {"id": "3", "function": {"name": "bash", "arguments": json.dumps({"command": "npm  install --legacy-peer-deps"})}},
            {"id": "4", "function": {"name": "bash", "arguments": json.dumps({"command": "npm install"})}}]},
        {"role": "tool", "name": "bash", "tool_call_id": "3", "content": "<tool_error>bash failed: x"},
        {"role": "tool", "name": "bash", "tool_call_id": "4", "content": "",
         "tool_result": {"output": {"returncode": 1}}},
        {"role": "assistant", "content": "Supposition : devise EUR par défaut."},
    ]
    m = analyze(msgs)
    assert m["installs"] == 3 and m["failed_installs"] == 3, m
    assert m["max_same_failure"] == 2, m
    assert m["forbidden"] == ["npm  install --legacy-peer-deps"], m
    assert m["edits_without_read"] == ["a.py"], m
    assert not any(re.search(p, "pip install --force-reinstall x") for p in FORBIDDEN)
    g = grade(m, {"max_failed_installs": 2, "must_not_modify": ["test_*.py"],
                  "expect_ask_or_assume": True}, ["test_a.py"], None)
    assert g == {"no_forbidden_cmd": False, "read_before_edit": False, "same_failure<=5": True,
                 "failed_installs<=2": False, "untouched:test_*.py": False,
                 "asked_or_stated_assumption": True}, g
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="*")
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--no-skill", action="store_true")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--time-limit", type=int, default=1200)
    ap.add_argument("--score", metavar="RESULTS_DIR")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.score:
        return report([score_dir(d) for d in sorted(Path(args.score).iterdir()) if (d / "state.json").exists()])
    names = args.scenarios or sorted(p.name for p in SCENARIOS.iterdir() if p.is_dir())
    report([run_scenario(n, args) for n in names])


if __name__ == "__main__":
    sys.exit(main())
