#!/usr/bin/env python3
"""Move office tasks through stages.json. Workers run in hc terminals; this script only reads and writes files.

  engine.py new <task> --repo PATH --test CMD [--allow RULE ...] [--base main]
  engine.py run <task>        advance until a person is needed, the task is blocked, or it is done
  engine.py approve <task>    a person approves the current design file (bound to its sha256)
  engine.py workers           show every task's stage from files only
  engine.py test <task>       run the task's test command in its repo/ (workers cannot cd there)
  engine.py preflight <task>  run repo-baseline's review preflight for pr.txt in its repo/, diff into pr.diff

Exit codes for run: 0 done, 2 no such task or another run of it is going, 3 waiting for a person, 4 blocked.
"""

import argparse
import fcntl
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
# The repo-baseline clone whose prompts/ and tools/ the stages use. A person writes its path in baseline.path
# (git ignores it, workers cannot write it); without the file the office checkout sits inside that clone.
_PATH = OFFICE / "baseline.path"
BASELINE = Path(_PATH.read_text(encoding="utf-8").strip()).expanduser() if _PATH.exists() else OFFICE.parent
# A relative or empty path would follow the working folder; a worker runs preflight from its task folder, which it can write.
if not BASELINE.is_absolute():
    sys.exit(f"{_PATH} must hold the absolute path of the repo-baseline clone")
BASELINE = BASELINE.resolve()
JOBS = Path(os.environ.get("OFFICE_JOBS", OFFICE / "jobs"))
STAGES = Path(os.environ.get("OFFICE_STAGES", PIPELINE / "stages.json"))
DONE, WAIT, BLOCKED = 0, 3, 4
# git the engine runs outside the sandbox over a task's repo/ ignores settings task code could plant, such as a
# committed nested repository whose config runs core.fsmonitor on `git status`. Env config wins over repository files.
_SAFE = {"core.fsmonitor": "false", "diff.ignoreSubmodules": "all", "core.hooksPath": "/dev/null",
         "fetch.recurseSubmodules": "false", "submodule.recurse": "false", "protocol.ext.allow": "never"}
SAFE_GIT = {**os.environ, "GIT_CONFIG_COUNT": str(len(_SAFE)),
            **{f"GIT_CONFIG_{kind}_{i}": v for i, kv in enumerate(_SAFE.items()) for kind, v in zip(("KEY", "VALUE"), kv)}}
# hc terminal names are global to the hc core; this tag keeps another checkout's jobs folder from sharing them.
TAG = hashlib.sha256(str(JOBS.resolve()).encode()).hexdigest()[:4]


class NotStarted(str):
    """An error from before the worker started: its session was never created, so the next try must not --resume."""


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
    if "sandbox" in config:
        at = argv.index("--settings") + 1
        argv[at] = json.dumps({**json.loads(argv[at]), "sandbox": sandbox(config["sandbox"], job, task)})
    if stage.get("model"):
        argv += ["--model", stage["model"]]
    return argv + ["--allowedTools", *tools, "--disallowedTools", *[fill(r, job, task) for r in config["deny"]]]


def sandbox(base, job, task):
    """Bash and what it starts (tests, git) may write the task folder and the commits of the product repository.

    The office checkout is an --add-dir for reading, and the sandbox makes such folders writable: deny all of it but
    jobs/. Excluded commands (gh, preflight, push) run outside the sandbox because they need the keychain; the git
    they run follows this task's git settings and hooks, so the sandbox must not let task code change those.
    """
    def git(*args):
        return subprocess.run(["git", "-C", str(job / "repo"), "rev-parse", "--path-format=absolute", *args],
                              capture_output=True, text=True).stdout.strip()
    common, own = git("--git-common-dir"), git("--git-dir")
    deny = [str(p) for p in BASELINE.iterdir() if p != OFFICE] + [str(p) for p in OFFICE.iterdir() if p != JOBS]
    deny += [str(p) for p in JOBS.iterdir() if p != job]  # other tasks' control files and repositories
    # Files that do not exist yet but that a later session or git would read as instructions or settings.
    for root in (BASELINE, OFFICE, job):
        deny += [str(root / n) for n in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude", ".mcp.json", ".envrc", ".git")]
    deny.append(str(_PATH))  # names the clone whose prompts and preflight the review stage runs
    # The engine's control files: the edit rules bind the Edit and Write tools only, not code a test runs.
    deny += [str(job / n) for n in ("task.json", "state.json", "pr.txt", "pr.diff", "run.lock")]
    # Where git takes settings and hooks: the worktree's gitdir pointer, the shared config and hooks, the worktree links.
    deny += [str(job / "repo/.git")]
    if common:
        deny += [f"{common}/{n}" for n in ("config", "hooks", "info")]
        # A denied folder also denies what an allow opens inside it (seen with Seatbelt), so list the other worktrees.
        links = Path(common) / "worktrees"
        deny += [str(p) for p in (links.iterdir() if links.is_dir() else ()) if str(p) != own]
    if own and own != common:
        deny += [f"{own}/{n}" for n in ("commondir", "gitdir", "config.worktree")]
    allow = [str(job), *([common] if common else [])]
    if own and own != common:
        allow.append(own)  # index, HEAD and logs of this worktree
    return {**base, "excludedCommands": [fill(c, job, task) for c in base.get("excludedCommands", [])],
            "filesystem": {"denyWrite": deny, "allowWrite": allow}}


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
        return NotStarted(error)
    # A terminal of this name is left over from an engine that died mid-stage: run() holds the task lock and the name
    # carries this jobs folder's TAG, so no live engine owns it. Its worker may still be writing: closing it is what a second try of the stage needs anyway.
    hc("kill", name, check=False)
    hc("rm", name, check=False)
    try:
        hc("run", "--bg", "--name", name, "--cwd", str(cwd), binary, *argv[1:])
    except subprocess.CalledProcessError as error:
        return NotStarted(f"hc run failed: {(error.stderr or error.stdout or '').strip()}")
    try:
        hc("wait", "--timeout", "90", name, check=False)
        if "trust this folder" in hc("read", "--lines", "0", name, check=False):
            return NotStarted("folder is not trusted: open Claude Code here once and trust it")
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
    """Why these checkouts may not review the task's PR, else None.

    The review worker gets its prompt, permissions and preflight script from the office and repo-baseline checkouts,
    and REVIEW.md from repo-baseline. For a PR of the same repository as either one, that checkout must be
    origin/main's copy, or the PR could rewrite its own review. Only a PR that provably belongs to another
    repository skips the comparison. An office checkout inside the repo-baseline clone is the same repository.
    """
    def git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)

    pr_file = job / "pr.txt"  # a person wrote it; workers cannot edit it
    pr = slug(pr_file.read_text(encoding="utf-8")) if pr_file.exists() else None
    product = git(task["repo"], "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    for root in [BASELINE] + ([] if OFFICE.is_relative_to(BASELINE) else [OFFICE]):
        own = slug(git(root, "remote", "get-url", "origin").stdout)
        common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        if pr and own and pr != own and common and product and common != product:
            continue
        git(root, "fetch", "-q", "origin", "main")  # offline: the comparison below uses the last fetched main
        # A missing origin/main makes diff fail, which also blocks. Ignored files (jobs/) are not compared.
        if git(root, "diff", "--quiet", "origin/main").returncode or git(
                root, "status", "--porcelain", "--untracked-files=all").stdout.strip():
            return (f"the PR under review is not known to belong to another repository, and {root} differs from "
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
    # Held until run returns or the process dies (kill -9 included). "a" leaves the mtime that workers shows alone.
    with open(job / "run.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"another engine.py run {name} is running", file=sys.stderr)
            return 2
        return advance(job, name, runner)


def advance(job, name, runner):
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
            git = lambda *a: subprocess.run(["git", *a], cwd=job / "repo", capture_output=True, text=True, env=SAFE_GIT).stdout.strip()  # noqa: E731
            # Read what the worker handed over before the test writes its own files (caches, reports).
            head, dirty = git("rev-parse", "HEAD"), git("status", "--porcelain", "--untracked-files=all")
            if not shutil.which("sandbox-exec"):
                return block(job, "sandbox-exec is missing: the check stage runs the worker's code only inside a sandbox")
            result = run_test(job, task, sandboxed=True, capture_output=True, text=True)
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
            error = runner(f"{TAG}-{name}-{stage['id']}"[:32], job, claude_argv(config, stage, job, task, state), f"Read {prompt} and follow it exactly.")
            if stage["session"] not in state["sessions"] and not isinstance(error, NotStarted):
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


# Words that, as a file or folder in the task folder, a later session or git reads as instructions or settings.
CONTROL = ("task.json", "state.json", "pr.txt", "pr.diff", "run.lock", "repo/.git", ".git",
           "CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude", ".mcp.json", ".envrc")


def check_profile(job):
    """Seatbelt rules for the check stage, the same writes the worker's sandbox allows: the task folder, minus its
    control files. No network but localhost and unix sockets in the task folder. No reads in the home folder (credentials) but the task folder, this
    checkout and the product's git data, like blockReadsOutsideWorkingDirectories for the worker. The worker's own `engine.py test` already runs in Claude Code's
    sandbox, which refuses a sandbox inside it, so only the engine's check stage uses this."""
    quote = json.dumps  # SBPL strings take the same escapes as JSON for quotes and backslashes
    real = job.resolve()
    deny = " ".join(f"(literal {quote(str(real / n))}) (subpath {quote(str(real / n))})" for n in CONTROL)
    deny += f" (literal {quote(str(real / 'repo'))})"  # renaming repo/ away and back would rewrite repo/.git
    common = subprocess.run(["git", "-C", str(job / "repo"), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, env=SAFE_GIT).stdout.strip()
    readable = " ".join(f"(subpath {quote(str(Path(p).resolve()))})" for p in (job, BASELINE, OFFICE, *([common] if common else [])))
    return ("(version 1) (allow default)\n"
            # Unix sockets only in the task folder: one outside, such as /var/run/docker.sock, can start processes outside the sandbox.
            f"(deny network-outbound) (allow network-outbound (remote ip \"localhost:*\") (remote unix-socket (subpath {quote(str(real))})))\n"
            f"(deny file-write*) (allow file-write* (subpath {quote(str(real))}) (subpath \"/dev\"))\n"
            f"(deny file-write* {deny})\n"
            f"(deny file-read* (subpath {quote(str(Path.home().resolve()))})) (allow file-read* {readable})\n")


def run_test(job, task, sandboxed=False, **kwargs):
    # The owner wrote this command (the product's AGENTS.md test cell) with `new`; workers cannot edit task.json.
    if not sandboxed:
        return subprocess.run(task["test"], shell=True, cwd=job / "repo", **kwargs)
    tmp = job / "tmp"  # temp files stay in the one folder the sandbox lets the test write
    home = tmp / "home"  # the profile hides the real home; tools that cache or read config there (Godot, git) need a writable one
    home.mkdir(parents=True, exist_ok=True)
    return subprocess.run(["sandbox-exec", "-p", check_profile(job), "/bin/sh", "-c", task["test"]], cwd=job / "repo",
                          env={**os.environ, "TMPDIR": str(tmp), "HOME": str(home), "GIT_CONFIG_GLOBAL": "/dev/null"}, **kwargs)


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
    result = subprocess.run([sys.executable, str(script), *pr[:1], "--json"], cwd=job / "repo", capture_output=True,
                            text=True, errors="replace", env=SAFE_GIT)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode:  # 1 means a secrets file changed: the review must not open the diff
        return result.returncode
    # The worker cannot run diff_argv: its absolute -C path has no allow rule, and the quoted pathspecs that keep
    # .env* out hit the quote deny rule. So the engine runs the same diff and hands over the text. It builds the
    # command from base and head, checked here, instead of running the printed list.
    try:
        found = json.loads(result.stdout)
        base, head = found["base"], found["head"]
        ok = (not found["secret_hits"] and isinstance(found["diff_argv"], list) and isinstance(base, str)
              and isinstance(head, str) and re.fullmatch(r"refs/remotes/[\w./-]+", base) and re.fullmatch(r"[0-9a-f]{40}", head))
    except (ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        print("the preflight output is not the expected JSON", file=sys.stderr)
        return 2
    argv = ["git", "-C", str(job / "repo"), "diff", f"{base}...{head}", "--", ".", ":!.env*", ":!**/.env*"]
    # Bytes, not text: a non-UTF-8 file in the PR would raise, and an uncaught error exits 1, the secrets-file code.
    diff = subprocess.run(argv, capture_output=True, env=SAFE_GIT)
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
        files = [p for p in job.iterdir() if p.is_file() and p.name != "run.lock"]
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
