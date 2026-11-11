#!/usr/bin/env python3
"""Move office tasks through stages.json. Workers run in hc terminals; this script only reads and writes files.

  engine.py new <task> --repo PATH --test CMD [--allow RULE ...] [--base main]
  engine.py run <task>        advance until a person is needed, the task is blocked, or it is done
  engine.py approve <task>    a person approves the current design file (bound to its sha256)
  engine.py workers           show every task's stage from files only
  engine.py test <task>       run the task's test command in its repo/ (workers cannot cd there)
  engine.py preflight <task>  run repo-baseline's review preflight for pr.txt in its repo/, diff into pr.diff

Exit codes for run: 0 done, 3 waiting for a person, 4 blocked.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
OFFICE = PIPELINE.parent
BASELINE = OFFICE.parent  # this checkout carries repo-baseline's prompts/ and tools/
JOBS = Path(os.environ.get("OFFICE_JOBS", OFFICE / "jobs"))
STAGES = Path(os.environ.get("OFFICE_STAGES", PIPELINE / "stages.json"))
DONE, WAIT, BLOCKED = 0, 3, 4


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def session_id(task, key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"claude-office:{task}:{key}"))


def fill(text, job, task):
    values = {"job": job, "office": OFFICE, "baseline": BASELINE, "name": task["name"], "branch": f"office/{task['name']}",
              "test": task["test"]}
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def claude_argv(config, stage, job, task, state):
    """The worker command: a new session for the first use of a session key, --resume after that."""
    key = stage["session"]
    flag = "--resume" if key in state["sessions"] else "--session-id"
    tools = []
    for rule in stage["tools"]:
        tools.extend(task["allow"] if rule == "{allow}" else [fill(rule, job, task)])
    argv = [*(fill(arg, job, task) for arg in config["runner"]), flag, session_id(task["name"], key)]
    if stage.get("model"):
        argv += ["--model", stage["model"]]
    return argv + ["--allowedTools", *tools, "--disallowedTools", *[fill(r, job, task) for r in config["deny"]]]


def hc(*args, check=True):
    return subprocess.run(["hc", *args], capture_output=True, text=True, check=check).stdout


def claude_too_old(version):
    """Older Claude Code ignores blockReadsOutsideWorkingDirectories without a word, so workers could read any file."""
    found = re.fullmatch(r"(\d+)\.(\d+)\.(\d+) \(Claude Code\)\s*", version)
    if found and tuple(map(int, found.groups())) >= (2, 1, 257):
        return None
    return f"Claude Code 2.1.257 or later is needed (blockReadsOutsideWorkingDirectories); `claude --version` gave: {version.strip() or 'nothing'}"


def run_worker(name, cwd, argv, message):
    """Start an interactive worker in hc, give it one short message, wait for the turn, then end it."""
    # hc resolves PATH on its own, so pin the binary whose version was checked.
    binary = shutil.which(argv[0])
    binary = binary and os.path.abspath(binary)  # which() keeps a relative PATH entry relative, and hc starts in cwd
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout if binary else ""
    except (OSError, ValueError, subprocess.TimeoutExpired):  # hung, unrunnable or not UTF-8: block like an unreadable version
        version = ""
    if error := claude_too_old(version):
        return error
    hc("run", "--bg", "--name", name, "--cwd", str(cwd), binary, *argv[1:])
    try:
        hc("wait", "--timeout", "90", name, check=False)
        if "trust this folder" in hc("read", "--lines", "0", name, check=False):
            return "folder is not trusted: open Claude Code here once and trust it"
        # A task passed on the command line is dropped after a dialog or --resume, so send it after start.
        reply = json.loads(hc("send", "--wait", name, message, check=False) or "{}").get("wait") or {}
        while reply.get("reason") == "timeout":
            reply = json.loads(hc("wait", name, check=False) or "{}")
        return None
    finally:
        hc("kill", name, check=False)  # close the session so the next stage can resume it
        hc("rm", name, check=False)


def block(job, reason):
    (job / "BLOCKED.md").write_text(f"# 막힘\n\n{reason}\n", encoding="utf-8")
    print(f"blocked: {reason}")
    return BLOCKED


def slug(url):
    """owner/repo of a GitHub URL (https, ssh or a PR link), else None."""
    found = re.search(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/\s]|$)", url)
    return f"{found[1]}/{found[2]}".lower() if found else None


def unpinned(job, task):
    """Why this checkout may not review the task's PR, else None.

    The review worker gets its prompt, permissions, preflight script and REVIEW.md from this checkout. For a
    PR of this same repository they must be origin/main's copies, or the PR could rewrite its own review.
    Only a PR that provably belongs to another repository skips the comparison.
    """
    def git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)

    pr_file = job / "pr.txt"  # a person wrote it; workers cannot edit it
    pr = slug(pr_file.read_text(encoding="utf-8")) if pr_file.exists() else None
    own = slug(git(BASELINE, "remote", "get-url", "origin").stdout)
    common = [git(d, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip() for d in (BASELINE, task["repo"])]
    if pr and own and pr != own and common[0] and common[1] and common[0] != common[1]:
        return None
    git(BASELINE, "fetch", "-q", "origin", "main")  # offline: the comparison below uses the last fetched main
    # A missing origin/main makes diff fail, which also blocks. Ignored files (jobs/) are not compared.
    if git(BASELINE, "diff", "--quiet", "origin/main").returncode or git(
            BASELINE, "status", "--porcelain", "--untracked-files=all").stdout.strip():
        return (f"the PR under review is not known to belong to another repository, and {BASELINE} differs from "
                "origin/main: run the task from a clean checkout at origin/main")
    return None


def missing(job, name, *files):
    """One-line error for the first absent task folder or file, else None."""
    if not job.is_dir():
        return f"no such task: {name}"
    for file in files:
        if not (job / file).exists():
            hint = f": run {name} first" if file == "state.json" else f": {job / file}"
            return f"{file} missing{hint}"
    return None


def run(name, runner=run_worker):
    job = JOBS / name
    error = missing(job, name, "task.json")
    if error:
        print(error, file=sys.stderr)
        return 2
    task, config = load(job / "task.json", None), load(STAGES, None)
    flow = config["flow"]
    ids = [stage["id"] for stage in flow]
    state_path = job / "state.json"
    state = load(state_path, {"stage": ids[0], "since": time.time(), "fails": {}, "sessions": []})

    def move(stage_id):
        state.update(stage=stage_id, since=time.time())
        save(state_path, state)

    while True:
        if (job / "BLOCKED.md").exists():
            print(f"blocked: see {job / 'BLOCKED.md'}")
            return BLOCKED
        if state["stage"] == "done":
            print("done: the owner opens or merges the PR (POLICY §5)")
            return DONE
        index = ids.index(state["stage"])
        # An approval binds the file's content: any later change sends the task back to that approval.
        stale = [s["id"] for s in flow[:index] if s.get("approve") and sha(job / s["approve"]) != state.get("approved", {}).get(s["id"])]
        if stale:
            move(stale[0])
            continue
        stage = flow[index]
        following = ids[index + 1] if index + 1 < len(ids) else "done"
        failed = None
        if stage["by"] == "human":
            if stage.get("approve") and sha(job / stage["approve"]) != state.get("approved", {}).get(stage["id"]):
                print(f"waiting: a person reads {job / stage['approve']} and runs engine.py approve {name}")
                return WAIT
            if stage.get("wait") and not (job / stage["wait"]).exists():
                print(f"waiting: a person writes {job / stage['wait']}")
                return WAIT
        elif stage["by"] == "script":
            git = lambda *a: subprocess.run(["git", *a], cwd=job / "repo", capture_output=True, text=True).stdout.strip()  # noqa: E731
            # Read what the worker handed over before the test writes its own files (caches, reports).
            head, dirty = git("rev-parse", "HEAD"), git("status", "--porcelain", "--untracked-files=all")
            result = run_test(job, task, capture_output=True, text=True)
            (job / stage["out"]).write_text(
                f"# {stage['id']}\n\n- 명령: `{task['test']}`\n- 커밋: `{head}`\n- exit {result.returncode}\n"
                f"- 커밋하지 않은 변경: {dirty or '없음'}\n\n```\n{(result.stdout + result.stderr)[-4000:]}\n```\n", encoding="utf-8")
            if result.returncode or dirty:
                failed = f"`{task['test']}` exit {result.returncode}" + (", uncommitted changes" if dirty else "")
        else:
            if stage["id"] == "review" and (reason := unpinned(job, task)):
                return block(job, reason)
            out = job / stage["out"]
            out.unlink(missing_ok=True)
            prompt = job / f"{stage['id']}.prompt.md"
            prompt.write_text(fill((PIPELINE / stage["prompt"]).read_text(encoding="utf-8"), job, task), encoding="utf-8")
            # The task folder sits inside the trusted office checkout; a fresh worktree would show the trust dialog.
            error = runner(f"{name}-{stage['id']}"[:32], job, claude_argv(config, stage, job, task, state), f"Read {prompt} and follow it exactly.")
            if stage["session"] not in state["sessions"]:
                state["sessions"].append(stage["session"])
                save(state_path, state)  # a blocked stage returns below; the next run must still --resume
            if error:
                return block(job, error)
            if (job / "BLOCKED.md").exists():
                continue
            if not out.exists():
                failed = f"{stage['id']} ended without writing {out.name}"
        if failed:
            state["fails"][stage["id"]] = state["fails"].get(stage["id"], 0) + 1
            save(state_path, state)
            if state["fails"][stage["id"]] >= 2:
                state["fails"].clear()  # a person who clears BLOCKED.md gets two fresh tries
                save(state_path, state)
                return block(job, f"{failed}, twice in a row")
            move(stage.get("retry", stage["id"]))
            continue
        state["fails"].pop(stage["id"], None)
        move(following)


def run_test(job, task, **kwargs):
    # The owner wrote this command (the product's AGENTS.md test cell) with `new`; workers cannot edit task.json.
    return subprocess.run(task["test"], shell=True, cwd=job / "repo", **kwargs)


def test(name):
    """A worker's Bash cd is refused while blockReadsOutsideWorkingDirectories is on, so the engine runs the test in repo/."""
    job = JOBS / name
    error = missing(job, name, "task.json")
    if error:
        print(error, file=sys.stderr)
        return 2
    return run_test(job, load(job / "task.json", None)).returncode


def preflight(name):
    """review_preflight.py reads the git repository of its working folder; a worker's folder is inside the office checkout."""
    job = JOBS / name
    error = missing(job, name, "task.json", "pr.txt")
    if error:
        print(error, file=sys.stderr)
        return 2
    pr = (job / "pr.txt").read_text(encoding="utf-8").split()  # a person wrote it; workers cannot edit it
    script = BASELINE / "tools/review_preflight.py"
    diff_file = job / "pr.diff"
    diff_file.unlink(missing_ok=True)
    result = subprocess.run([sys.executable, str(script), *pr[:1]], cwd=job / "repo", capture_output=True, text=True,
                            errors="replace")
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode:  # 1 means a secrets file changed: the review must not open the diff
        return result.returncode
    # The worker cannot run the printed diff command: its absolute -C path has no allow rule, and the quoted
    # pathspecs that keep .env* out hit the quote deny rule. So the engine runs the same diff and hands over the text.
    # Earlier lines carry PR file names, which may hold a forged "diff:" line: only the last line counts, it must match
    # exactly, and the engine builds the command from its base and head instead of running what was printed.
    lines = result.stdout.splitlines()
    found = re.fullmatch(r"diff: git -C .+ diff (refs/remotes/[\w./-]+)\.\.\.([0-9a-f]{40}) -- \. ':!\.env\*' ':!\*\*/\.env\*'",
                         lines[-1] if lines else "")
    if not found:
        print("no diff command on the last line of the preflight output", file=sys.stderr)
        return 2
    argv = ["git", "-C", str(job / "repo"), "diff", f"{found[1]}...{found[2]}", "--", ".", ":!.env*", ":!**/.env*"]
    # Bytes, not text: a non-UTF-8 file in the PR would raise, and an uncaught error exits 1, the secrets-file code.
    diff = subprocess.run(argv, capture_output=True)
    if diff.returncode:
        print(f"diff failed: {diff.stderr.decode(errors='replace').strip()}", file=sys.stderr)
        return 2
    diff_file.write_bytes(diff.stdout)
    print(f"diff 본문: {diff_file}")
    return 0


def approve(name):
    job = JOBS / name
    error = missing(job, name, "task.json", "state.json")
    if error:
        print(error, file=sys.stderr)
        return 2
    state_path = job / "state.json"
    state = load(state_path, None)
    stage = next(s for s in load(STAGES, None)["flow"] if s["id"] == state["stage"])
    if not stage.get("approve") or not (job / stage["approve"]).exists():
        print(f"nothing to approve at stage {state['stage']}", file=sys.stderr)
        return 2
    state.setdefault("approved", {})[stage["id"]] = sha(job / stage["approve"])
    save(state_path, state)
    print(f"approved {stage['approve']} {state['approved'][stage['id']]}")
    return 0


def new(name, repo, test, allow, base):
    # The name becomes a folder, a branch and an hc terminal name (a-z 0-9 - _ only).
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,23}", name):
        print("task name: a-z 0-9 - _, at most 24 characters", file=sys.stderr)
        return 2
    job = JOBS / name
    job.mkdir(parents=True)
    save(job / "task.json", {"name": name, "repo": str(Path(repo).resolve()), "test": test, "allow": allow, "base": base})
    (job / "INTENT.md").write_text(
        f"# {name}\n\n## 목표\n\n## 지시 원문\n\n## 하지 않을 것\n\n## 결정 기록\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "worktree", "add", "-b", f"office/{name}", str(job / "repo"), base], check=True)
    print(job / "INTENT.md")
    return 0


def workers():
    print(f"{'과제':<24} {'단계':<10} {'단계 경과':>9}  마지막 파일")
    for job in sorted(p for p in JOBS.glob("*") if (p / "task.json").exists()):
        state = load(job / "state.json", {"stage": "-", "since": time.time()})
        stage = "blocked" if (job / "BLOCKED.md").exists() else state["stage"]
        files = [p for p in job.iterdir() if p.is_file()]
        last = max(files, key=lambda p: p.stat().st_mtime)
        minutes = int((time.time() - state["since"]) // 60)
        print(f"{job.name:<24} {stage:<10} {minutes:>7}분  {last.name} {datetime.fromtimestamp(last.stat().st_mtime):%H:%M}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    created = sub.add_parser("new")
    created.add_argument("task")
    created.add_argument("--repo", required=True)
    created.add_argument("--test", required=True, help="the product's AGENTS.md test command")
    created.add_argument("--allow", action="append", default=[], help="extra tool rule run from the task folder, e.g. 'Bash(make -C repo lint)'")
    created.add_argument("--base", default="main")
    for command in ("run", "approve", "test", "preflight"):
        sub.add_parser(command).add_argument("task")
    sub.add_parser("workers")
    args = parser.parse_args(argv)
    if args.command == "new":
        return new(args.task, args.repo, args.test, args.allow, args.base)
    if args.command == "workers":
        return workers()
    return {"run": run, "approve": approve, "test": test, "preflight": preflight}[args.command](args.task)


if __name__ == "__main__":
    sys.exit(main())
