#!/usr/bin/env python3
"""Move office tasks through stages.json. Workers run in hc terminals; this script only reads and writes files.

  engine.py new <task> --repo PATH --test CMD [--allow RULE ...] [--base main]
  engine.py run <task>        advance until a person is needed, the task is blocked, or it is done
  engine.py approve <task>    a person approves the current design file (bound to its sha256)
  engine.py workers           show every task's stage from files only
  engine.py test <task>       run the task's test command in its repo/ (workers cannot cd there)
  engine.py preflight <task>  run repo-baseline's review preflight for pr.txt in its repo/, diff into pr.diff
  engine.py skills <task> <base>  run matt-skills check_skills.py in its repo/ (exit 0 delegate, 1 missing, 2 do not)

After the check passes, run pushes the task branch and opens a draft PR from pr-body.md; a person marks it ready.
After the review, run replaces the PR body with review-body.md.

Exit codes for run: 0 done, 2 no such task or another run of it is going, 3 waiting for a person, 4 blocked.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
OFFICE = PIPELINE.parent
# The repo-baseline clone whose prompts/ and tools/ the stages use. A person writes its path in baseline.path
# (git ignores it, workers cannot write it). Without the file, main() stops before any command but workers.
_PATH = OFFICE / "baseline.path"
BASELINE = Path(_PATH.read_text(encoding="utf-8").strip()).expanduser() if _PATH.exists() else None
# A relative or empty path would follow the working folder; a worker runs preflight from its task folder, which it can write.
if BASELINE is not None and not BASELINE.is_absolute():
    sys.exit(f"{_PATH} must hold the absolute path of the repo-baseline clone")
BASELINE = BASELINE and BASELINE.resolve()
JOBS = Path(os.environ.get("OFFICE_JOBS", OFFICE / "jobs"))
STAGES = Path(os.environ.get("OFFICE_STAGES", PIPELINE / "stages.json"))
DONE, WAIT, BLOCKED = 0, 3, 4
# git the engine runs outside the sandbox over a task's repo/ ignores settings task code could plant, such as a
# committed nested repository whose config runs core.fsmonitor on `git status`. Env config wins over repository files.
# Replace refs, which task code can write, would show git a different history. The owner's global push settings
# would push more than the branch: tags task code made, or committed submodules.
_SAFE = {"core.fsmonitor": "false", "diff.ignoreSubmodules": "all", "core.hooksPath": "/dev/null",
         "fetch.recurseSubmodules": "false", "submodule.recurse": "false", "protocol.ext.allow": "never",
         "push.followTags": "false", "push.recurseSubmodules": "no"}
SAFE_GIT = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_CONFIG_COUNT": str(len(_SAFE)),
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
    # blockReadsOutsideWorkingDirectories also closes reads for sandboxed Bash, and a product repository outside
    # /tmp keeps repo/'s git data out of reach: every git command then fails with "not a git repository".
    return {**base, "excludedCommands": [fill(c, job, task) for c in base.get("excludedCommands", [])],
            "filesystem": {"denyWrite": deny, "allowWrite": allow, "allowRead": allow[1:]}}


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
    repository skips the comparison.
    """
    def git(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)

    pr_file = job / "pr.txt"  # the draft stage or a person wrote it; workers cannot edit it
    pr = slug(pr_file.read_text(encoding="utf-8")) if pr_file.exists() else None
    product = git(task["repo"], "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    for root in (BASELINE, OFFICE):  # the same repository twice gives the same answer twice
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


def read_body(body):
    """The PR body's bytes, or b"" unless it is a regular file with one name of at most 60000 bytes.
    The worker wrote it inside its sandbox, and the engine reads it outside and posts it: a link could point at
    a file the sandbox hides (credentials). So it is opened without following links. O_NONBLOCK: a FIFO would wait for a writer."""
    try:
        fd = os.open(body, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return b""
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        ok = stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 60000
        return file.read() if ok else b""


def open_draft(job, task, stage, raw, head, approved=None):
    """Push the task branch and open a draft PR with the body the implement worker wrote, else why not.
    raw and head are what the caller already checked (the body's bytes, the tested commit): nothing here reads them again.
    approved is the sha256 a person approved for the stage's easy file."""
    pr_file = job / stage["out"]
    if pr_file.exists():  # an earlier run opened it, or a person did and wrote the URL
        return None
    body = job / stage["body"]
    title, _, text = raw.decode("utf-8", "replace").partition("\n")
    if not title.startswith("# ") or not title[2:].strip():
        return f"{body} must be a regular file of at most 60000 bytes that starts with a `# <PR title>` line"
    git = lambda *a: subprocess.run(["git", "-C", str(job / "repo"), *a], capture_output=True, text=True, env=SAFE_GIT)  # noqa: E731
    repo = slug(git("config", "--get", "remote.origin.url").stdout)  # as written: url.*.insteadOf may point elsewhere
    if not repo:
        return "the product repository's origin is not on GitHub: open the PR and write its URL into pr.txt"
    # A pushed branch runs the repository's workflows, with their secrets: a person pushes changes to them. The
    # worker can move local refs, origin/main included, so compare with the base commit the remote has now.
    base = task["base"].removeprefix("origin/")
    remote = git("ls-remote", "origin", f"refs/heads/{base}").stdout.split()[:1]
    if not remote:
        return f"origin has no branch {base} to compare the task branch with"
    git("fetch", "-q", "origin", f"refs/heads/{base}")  # others may have pushed to it since this checkout was made
    ci = git("diff", "--name-only", f"{remote[0]}...{head}", "--", ".github")  # the commit that is pushed below
    if ci.returncode or not head:
        return f"could not compare the task branch with origin's {base}: {ci.stderr.strip()}"
    if ci.stdout.strip():
        return (f"the branch changes .github ({ci.stdout.split()}): a person reads the diff, "
                "pushes the branch, opens the PR and writes its URL into pr.txt")
    branch = f"office/{task['name']}"
    # No -u: it writes the product repository's shared config. No force: a diverged branch of that name stops here.
    push = git("push", "origin", f"{head}:refs/heads/{branch}")
    if push.returncode:
        return f"git push failed: {push.stderr.strip()}"
    made = subprocess.run(["gh", "pr", "create", "--repo", repo, "--draft", "--head", branch,
                           "--base", base, "--title", title[2:].strip(),
                           "--body", text.lstrip("\n")], cwd=job, capture_output=True, text=True)
    url = (made.stdout.strip().splitlines() or [""])[-1]
    if made.returncode or not re.fullmatch(rf"https://github\.com/{re.escape(repo)}/pull/\d+", url, re.I):
        return f"gh pr create failed: {(made.stderr or made.stdout).strip()}"
    pr_file.write_text(url + "\n", encoding="utf-8")
    # An easy PR skips the person's Ready: they already approved the design that says so. A hard one waits for them,
    # and Ready is when they ask a skipping review bot for a review (POLICY §3). A failed gh leaves the draft to them.
    if stage.get("easy"):
        # The bytes read here must be the approved ones: a process the tested code left could rewrite the file since run() checked.
        design = read_body(job / stage["easy"])
        # -z: raw names, not C-quoted ones; --no-renames: a rename out of a trust path shows its old name too
        names = git("diff", "--name-only", "-z", "--no-renames", f"{remote[0]}...{head}")
        guarded = names.returncode or any(part.casefold() in TRUST_NAMES for n in names.stdout.split("\0") for part in n.split("/"))
        if approved and hashlib.sha256(design).hexdigest() == approved and easy(design, raw) and not guarded:
            subprocess.run(["gh", "pr", "ready", url], cwd=job, capture_output=True)
    return None


# Paths a worker could use to steer later sessions or tools, whatever the body claims: those PRs wait for a person.
INSTRUCTIONS = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude", ".mcp.json", ".envrc")
TRUST_NAMES = {n.casefold() for n in (*INSTRUCTIONS, ".github", ".agents", ".codex", ".cursor", "REVIEW.md")}


def easy(design, body):
    """True only when every line of the approved design and of the PR body that names Door says exactly
    Door: 되돌리기 쉬움 (a heading aside), there is one, and the body has exactly one line naming a trust boundary: 신뢰 경계: 없음.
    Fails closed: list markers, emphasis or a second claim anywhere leave the PR a draft."""
    def claims(raw, word, value):
        found = [l.strip() for l in unicodedata.normalize("NFKC", raw.decode("utf-8", "replace")).splitlines()
                 if re.search(word, l, re.I) and not re.match(r"\s*#+\s*\S+\s*$", l)]
        return bool(found) and set(found) == {value}
    door, trust = r"d\W*o\W*o\W*r", r"신뢰\W*경계"
    return (claims(design, door, "Door: 되돌리기 쉬움") and claims(body, door, "Door: 되돌리기 쉬움")
            and claims(body, trust, "신뢰 경계: 없음")
            and sum(bool(re.search(trust, l)) for l in body.decode("utf-8", "replace").splitlines()) == 1)


def push_review(job, task):
    """Push the review worker's commits to the PR branch, else why not.
    The worker cannot push: excluded from the sandbox or not, its git cannot read the owner's gh login (seen 2026-11-13)."""
    git = lambda *a: subprocess.run(["git", "-C", str(job / "repo"), *a], capture_output=True, text=True, env=SAFE_GIT)  # noqa: E731
    # The engine names the branch: tests in the sandbox can move this worktree's HEAD and local refs.
    branch = f"refs/heads/office/{task['name']}"
    remote = git("ls-remote", "origin", branch).stdout.split()[:1]
    if not remote:
        return f"origin has no {branch}: a person pushes the review's commits to the PR branch"
    head = git("rev-parse", "HEAD").stdout.strip()
    if head == remote[0]:
        return None
    git("fetch", "-q", "origin", branch)
    # Only what the review added: the draft stage or a person already let the rest of the branch onto GitHub.
    ci = git("diff", "--name-only", remote[0], head, "--", ".github")
    if ci.returncode or not head:
        return f"could not compare the review's commits with origin's {branch}: {ci.stderr.strip()}"
    if ci.stdout.strip():
        return (f"the review's commits change .github ({ci.stdout.split()}): a person reads the diff and pushes them")
    push = git("push", "origin", f"{head}:{branch}")  # no force: a diverged branch stops here
    if push.returncode:
        return f"git push failed: {push.stderr.strip()}"
    return None


def post_body(job, task, stage):
    """Push the review's commits, then replace the PR body with the file the review worker wrote, else why not.
    gh gets the bytes on stdin, never a path: it runs outside the sandbox and would follow a link to a hidden file."""
    body = job / stage["edit"]
    raw = read_body(body)
    pr_file = job / "pr.txt"  # the draft stage or a person wrote it; workers cannot edit it
    url = pr_file.read_text(encoding="utf-8").split()[:1] if pr_file.exists() else []
    if not raw.strip():
        return f"{body} must be a regular file of 1 to 60000 bytes with the whole PR body: the review worker writes it"
    if not url or not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", url[0]):
        return f"{pr_file} must hold the PR's GitHub URL"
    # The body names the reviewed head, and the owner merges that SHA: it goes up only once the commits are there.
    if error := push_review(job, task):
        return error
    edit =subprocess.run(["gh", "pr", "edit", url[0], "--body-file", "-"], input=raw, cwd=job, capture_output=True)
    if edit.returncode:
        return f"gh pr edit failed: {(edit.stderr or edit.stdout).decode(errors='replace').strip()}"
    return None


def not_ready(pr_file):
    """What a person still has to do with the PR in pr_file before the review, else None."""
    if not pr_file.exists():
        return f"waiting: a person opens the PR and writes its URL into {pr_file}"
    url = pr_file.read_text(encoding="utf-8").split()[:1]
    view = subprocess.run(["gh", "pr", "view", *url, "--json", "isDraft", "--jq", ".isDraft"], cwd=pr_file.parent,
                          capture_output=True, text=True)
    if view.returncode:
        return f"waiting: gh pr view {' '.join(url)} failed: {view.stderr.strip()}"
    if view.stdout.strip() != "false":
        return f"waiting: a person reads the draft PR {' '.join(url)} and marks it Ready for review, or closes it"
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
            print("done: the owner merges the PR (POLICY §5)")
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
            if stage.get("ready") and (waiting := not_ready(job / stage["ready"])):
                print(waiting)
                return WAIT
        elif stage["by"] == "script" and stage.get("body"):
            # Push only the commit the check stage passed: a commit made since (a rerun after a block) goes through it again.
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=job / "repo", capture_output=True, text=True, env=SAFE_GIT).stdout.strip()
            if not (job / stage["out"]).exists() and head != state.get("checked"):
                check = next((s["id"] for s in reversed(flow[:index]) if s["by"] == "script" and not s.get("body")), None)
                if not check:
                    return block(job, "no check stage before the draft stage: push only a tested commit")
                move(check)
                continue
            # The tested code cannot write the body by name, but a hard link it makes and removes again would change it.
            # A changed body blocks, and its new hash becomes the one to match: a person reads that file, clears
            # BLOCKED.md, and the engine posts it only if it is still what they read. No hash at all fails closed.
            raw = read_body(job / stage["body"])
            expected = state.get("bodies", {}).get(stage["body"])
            if not (job / stage["out"]).exists() and hashlib.sha256(raw).hexdigest() != expected:
                if expected is None:
                    check = next(s["id"] for s in reversed(flow[:index]) if s["by"] == "script" and not s.get("body"))
                    move(check)
                    continue
                state.setdefault("bodies", {})[stage["body"]] = hashlib.sha256(raw).hexdigest()
                save(state_path, state)
                return block(job, f"{stage['body']} changed after the check stage read it: a person reads it before the engine posts it")
            # The commit compared above, not HEAD again: HEAD can move between here and the push.
            approved = next((state.get("approved", {}).get(s["id"]) for s in flow if stage.get("easy") and s.get("approve") == stage["easy"]), None)
            if error := open_draft(job, task, stage, raw, state["checked"], approved):
                return block(job, error)
        elif stage["by"] == "script" and stage.get("edit"):
            if error := post_body(job, task, stage):
                return block(job, error)
        elif stage["by"] == "script":
            git = lambda *a: subprocess.run(["git", *a], cwd=job / "repo", capture_output=True, text=True, env=SAFE_GIT).stdout.strip()  # noqa: E731
            # Read what the worker handed over before the test writes its own files (caches, reports).
            head, dirty = git("rev-parse", "HEAD"), git("status", "--porcelain", "--untracked-files=all")
            bodies = [s["body"] for s in flow if s.get("body")]
            # What the draft stage may post: the bodies as they are before the tested code runs.
            body_shas = {b: hashlib.sha256(read_body(job / b)).hexdigest() for b in bodies}
            if not shutil.which("sandbox-exec"):
                return block(job, "sandbox-exec is missing: the check stage runs the worker's code only inside a sandbox")
            # The engine posts the PR body right after this stage, outside any sandbox: the tested code must not write it.
            # Nor the approved files: the draft stage reads them to decide the Ready.
            approves = [s["approve"] for s in flow if s.get("approve")]
            result = run_test(job, task, sandboxed=True, protect=[*bodies, *approves], capture_output=True, text=True)
            (job / stage["out"]).write_text(
                f"# {stage['id']}\n\n- 명령: `{task['test']}`\n- 커밋: `{head}`\n- exit {result.returncode}\n"
                f"- 커밋하지 않은 변경: {dirty or '없음'}\n\n```\n{(result.stdout + result.stderr)[-4000:]}\n```\n", encoding="utf-8")
            if result.returncode or dirty:
                failed = f"`{task['test']}` exit {result.returncode}" + (", uncommitted changes" if dirty else "")
            else:
                state.update(checked=head, bodies=body_shas)  # saved by move() below
        else:
            if stage["id"] == "review" and (reason := unpinned(job, task)):
                return block(job, reason)
            out = job / stage["out"]
            out.unlink(missing_ok=True)
            # A body left from an earlier try must not be posted as this try's.
            if following != "done" and (edit := flow[index + 1].get("edit")):
                (job / edit).unlink(missing_ok=True)
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
CONTROL = ("task.json", "state.json", "pr.txt", "pr.diff", "run.lock", "repo/.git", ".git", *INSTRUCTIONS)


def check_profile(job, protect=()):
    """Seatbelt rules for the check stage, the same writes the worker's sandbox allows: the task folder, minus its
    control files. No network but localhost and unix sockets in the task folder. No reads in the home folder (credentials) but the task folder, this
    checkout and the product's git data, like blockReadsOutsideWorkingDirectories for the worker. The worker's own `engine.py test` already runs in Claude Code's
    sandbox, which refuses a sandbox inside it, so only the engine's check stage uses this."""
    quote = json.dumps  # SBPL strings take the same escapes as JSON for quotes and backslashes
    real = job.resolve()
    deny = " ".join(f"(literal {quote(str(real / n))}) (subpath {quote(str(real / n))})" for n in (*CONTROL, *protect))
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


def run_test(job, task, sandboxed=False, protect=(), **kwargs):
    # The owner wrote this command (the product's AGENTS.md test cell) with `new`; workers cannot edit task.json.
    # The check stage fails on untracked files, and a worker's earlier test run would leave __pycache__ in repo/.
    # shortcut: Python bytecode only; other tools' artifacts must be in the product's .gitignore.
    tmp = job / "tmp"  # temp files stay in the one folder both sandboxes let the test write
    # Both sandboxes (the worker's Claude Code one and the check stage's) block the real home;
    # tools that cache or read config there (Godot, git) need a writable one.
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(tmp), "HOME": str(home), "GIT_CONFIG_GLOBAL": "/dev/null"}
    if not sandboxed:
        return subprocess.run(task["test"], shell=True, cwd=job / "repo", env=env, **kwargs)
    return subprocess.run(["sandbox-exec", "-p", check_profile(job, protect), "/bin/sh", "-c", task["test"]], cwd=job / "repo",
                          env=env, **kwargs)


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
    pr = (job / "pr.txt").read_text(encoding="utf-8").split()  # the draft stage or a person wrote it; workers cannot edit it
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


def skills(name, base):
    """matt-skills check_skills.py, like preflight, reads the git repository of its working folder."""
    job = JOBS / name
    error = missing(job, name, "task.json")
    # The base ref preflight printed, nothing else: no options, no `..` revision ranges. The worker runs this inside
    # its sandbox; moving it to excludedCommands needs this script reviewed like preflight.
    if error or not re.fullmatch(r"refs/remotes/[\w/-]+(\.[\w-]+)*", base) or "//" in base:
        print(error or f"base must be the preflight's base ref, got {base!r}", file=sys.stderr)
        return 2
    script = BASELINE / "profiles/matt-skills/check_skills.py"
    return subprocess.run([sys.executable, "-I", str(script), base, "--harness", "claude"], cwd=job / "repo",
                          env=SAFE_GIT).returncode


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
    checked = sub.add_parser("skills")
    checked.add_argument("task")
    checked.add_argument("base")
    sub.add_parser("workers")
    args = parser.parse_args(argv)
    if BASELINE is None and args.command != "workers":
        print(f"write the absolute path of the repo-baseline clone into {_PATH}", file=sys.stderr)
        return 2
    if args.command == "new":
        return new(args.task, args.repo, args.test, args.allow, args.base)
    if args.command == "workers":
        return workers()
    if args.command == "skills":
        return skills(args.task, args.base)
    return {"run": run, "approve": approve, "test": test, "preflight": preflight}[args.command](args.task)


if __name__ == "__main__":
    sys.exit(main())
