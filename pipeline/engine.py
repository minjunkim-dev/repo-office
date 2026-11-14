#!/usr/bin/env python3
"""Move office tasks through stages.json. Workers run in hc terminals; this script only reads and writes files.

  engine.py doctor <repo>     check that a product repository can connect (AGENTS.md cells pass on main, protection, gh)
  engine.py new <task> --repo PATH [--base main]   test = the product AGENTS.md check && test cells; runs setup once
  engine.py run <task>        advance until a person is needed, the task is blocked, or it is done
  engine.py approve <task>    a person approves the current design file (bound to its sha256)
  engine.py workers           show every task's stage from files only
  engine.py test <task>       run the task's test command in its repo/, full output in test.log
  engine.py preflight <task>  run repo-baseline's review preflight for pr.txt in its repo/, diff into pr.diff
  engine.py skills <task> <base>  run matt-skills check_skills.py in its repo/ (exit 0 delegate, 1 missing, 2 do not)

After the check passes, run pushes the task branch, opens a PR from pr-body.md, marks it ready and asks the review bot once.
After the review, run replaces the PR body with review-body.md and writes REPORT.md. People act only to approve the design and to merge.

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
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
OFFICE = PIPELINE.parent
# The repo-baseline clone whose prompts/ and tools/ the stages use. A person writes its path in baseline.path
# (git ignores it). Without the file, main() stops before any command but workers.
_PATH = OFFICE / "baseline.path"
BASELINE = Path(_PATH.read_text(encoding="utf-8").strip()).expanduser() if _PATH.exists() else None
# A relative or empty path would follow the working folder; a worker runs preflight from its task folder.
if BASELINE is not None and not BASELINE.is_absolute():
    sys.exit(f"{_PATH} must hold the absolute path of the repo-baseline clone")
BASELINE = BASELINE and BASELINE.resolve()
JOBS = Path(os.environ.get("OFFICE_JOBS", OFFICE / "jobs"))
STAGES = Path(os.environ.get("OFFICE_STAGES", PIPELINE / "stages.json"))
DONE, WAIT, BLOCKED = 0, 3, 4
# Errors a second try may clear (network, GitHub, hc): the engine retries them once on its own, after this many seconds.
TRANSIENT = ("git push failed", "gh pr create failed", "gh pr edit failed", "gh pr ready failed", "gh pr comment failed",
             "gh pr view", "hc run failed")
RETRY_WAIT = float(os.environ.get("OFFICE_RETRY_WAIT", "10"))
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


def session_key(stage, state):
    """A stage rerun with sonnet after haiku failed starts a session of its own: the failed one's context stays behind."""
    return stage["session"] + ("-sonnet" if stage["id"] in state.get("escalated", []) else "")


def difficulty(job, state, flow):
    """쉬움, 보통 or 어려움 from the approved design's one `난이도:` line, else None (missing, invalid, or not the approved bytes)."""
    stage = next((s for s in flow if s.get("approve")), None)
    if not stage or sha(job / stage["approve"]) != state.get("approved", {}).get(stage["id"]):
        return None
    lines = [l.strip() for l in unicodedata.normalize("NFKC", (job / stage["approve"]).read_text(encoding="utf-8", errors="replace")).splitlines()
             if "난이도" in l and not re.match(r"\s*#+\s*\S+\s*$", l)]
    found = {re.fullmatch(r"난이도: (쉬움|보통|어려움)", l) and l.split(": ")[1] for l in lines}
    return found.pop() if len(found) == 1 and None not in found else None


def stage_model(stage, job, state, flow):
    """The model a worker stage runs on. Workers get --setting-sources project, so the user's model setting never reaches them.
    implement: haiku for 쉬움, else sonnet, and sonnet after a failed haiku run. review: opus unless the PR is provably easy."""
    route = stage.get("model", "sonnet")
    if route == "difficulty":
        return "sonnet" if stage["id"] in state.get("escalated", []) or difficulty(job, state, flow) != "쉬움" else "haiku"
    if route == "door":
        # Proven easy for the commit that was checked and pushed: a later commit or a rerun of the check drops it.
        return "sonnet" if state.get("easy_pr") and state.get("easy_pr") == state.get("checked") else "opus"
    return route


def claude_argv(config, stage, job, task, state):
    """The worker command: a new session for the first use of a session key, --resume after that."""
    key = session_key(stage, state)
    flag = "--resume" if key in state["sessions"] else "--session-id"
    argv = [*(fill(arg, job, task) for arg in config["runner"]), flag, session_id(task["name"], key),
            "--model", stage_model(stage, job, state, config["flow"])]
    # Normal development tools; deny rules hold in every permission mode, so only the engine pushes or edits the PR.
    return argv + ["--allowedTools", *config["allow"], "--disallowedTools", *config["deny"]]


def hc(*args, check=True):
    return subprocess.run(["hc", *args], capture_output=True, text=True, check=check).stdout


def run_worker(name, cwd, argv, message):
    """Start an interactive worker in hc, give it one short message, wait for the turn, then end it."""
    # A terminal of this name is left over from an engine that died mid-stage: run() holds the task lock and the name
    # carries this jobs folder's TAG, so no live engine owns it. Its worker may still be writing: closing it is what a second try of the stage needs anyway.
    hc("kill", name, check=False)
    hc("rm", name, check=False)
    try:
        hc("run", "--bg", "--name", name, "--cwd", str(cwd), *argv)
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
    """The file's bytes, or b"" when it is missing or over 60000 bytes (GitHub's PR body limit is 65536 characters)."""
    return body.read_bytes() if body.is_file() and body.stat().st_size <= 60000 else b""


def open_draft(job, task, stage, raw, head, approved=None, seen=None):
    """Push the task branch and open a draft PR with the body the implement worker wrote, else why not.
    raw and head are what the caller already checked (the body's bytes, the tested commit): nothing here reads them again.
    approved is the sha256 a person approved for the stage's easy file. seen["easy"] says whether the PR proved easy."""
    pr_file = job / stage["out"]
    if pr_file.exists():  # an earlier run opened it, or a person did and wrote the URL
        return None
    body = job / stage["body"]
    title, _, text = raw.decode("utf-8", "replace").partition("\n")
    if not title.startswith("# ") or not title[2:].strip():
        return f"{body} must be a file of at most 60000 bytes that starts with a `# <PR title>` line"
    git = lambda *a: subprocess.run(["git", "-C", str(job / "repo"), *a], capture_output=True, text=True)  # noqa: E731
    repo = slug(git("config", "--get", "remote.origin.url").stdout)  # as written: url.*.insteadOf may point elsewhere
    if not repo:
        return "the product repository's origin is not on GitHub: open the PR and write its URL into pr.txt"
    # A pushed branch runs the repository's workflows, with their secrets: a person pushes changes to them.
    # Compare with the base commit the remote has now, not a local ref.
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
    # The ready stage marks every PR ready. Easy (proven here) only picks the review's model and the report's note:
    # a hard or trust-boundary PR needs a person to read its diff before the merge.
    if stage.get("easy"):
        design = read_body(job / stage["easy"])  # only the bytes a person approved
        # -z: raw names, not C-quoted ones; --no-renames: a rename out of a trust path shows its old name too
        names = git("diff", "--name-only", "-z", "--no-renames", f"{remote[0]}...{head}")
        guarded = names.returncode or any(part.casefold() in TRUST_NAMES for n in names.stdout.split("\0") for part in n.split("/"))
        if approved and hashlib.sha256(design).hexdigest() == approved and easy(design, raw) and not guarded:
            if seen is not None:
                seen["easy"] = True
    return None


# Paths a worker could use to steer later sessions or tools, whatever the body claims: those PRs wait for a person.
TRUST_NAMES = {n.casefold() for n in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", ".claude", ".mcp.json", ".envrc",
                                      ".github", ".agents", ".codex", ".cursor", "REVIEW.md")}


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
    """Push the review worker's commits to the PR branch, else why not. Only the engine pushes (workers' push is denied)."""
    git = lambda *a: subprocess.run(["git", "-C", str(job / "repo"), *a], capture_output=True, text=True)  # noqa: E731
    # The engine names the branch: the worktree's HEAD or local refs may have moved.
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
    """Push the review's commits, then replace the PR body with the file the review worker wrote, else why not."""
    body = job / stage["edit"]
    raw = read_body(body)
    pr_file = job / "pr.txt"  # the draft stage or a person wrote it
    url = pr_file.read_text(encoding="utf-8").split()[:1] if pr_file.exists() else []
    if not raw.strip():
        return f"{body} must be a file of 1 to 60000 bytes with the whole PR body: the review worker writes it"
    if not url or not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", url[0]):
        return f"{pr_file} must hold the PR's GitHub URL"
    # The body names the reviewed head, and the owner merges that SHA: it goes up only once the commits are there.
    if error := push_review(job, task):
        return error
    edit = subprocess.run(["gh", "pr", "edit", url[0], "--body-file", "-"], input=raw, cwd=job, capture_output=True)
    if edit.returncode:
        return f"gh pr edit failed: {(edit.stderr or edit.stdout).decode(errors='replace').strip()}"
    return None


def make_ready(job, stage, state):
    """Mark the PR in pr.txt ready and ask the review bot once (POLICY §3), else why not."""
    pr_file = job / stage["ready"]
    if not pr_file.exists():
        return f"{pr_file} is missing: the draft stage did not open a PR"
    url = pr_file.read_text(encoding="utf-8").split()[:1]
    view = subprocess.run(["gh", "pr", "view", *url, "--json", "isDraft", "--jq", ".isDraft"], cwd=job, capture_output=True, text=True)
    if view.returncode:
        return f"gh pr view {' '.join(url)} failed: {view.stderr.strip()}"
    if view.stdout.strip() != "false":
        ready = subprocess.run(["gh", "pr", "ready", *url], cwd=job, capture_output=True, text=True)
        if ready.returncode:
            return f"gh pr ready failed: {(ready.stderr or ready.stdout).strip()}"
    if stage.get("request") and state.get("requested") != url[0]:
        asked = subprocess.run(["gh", "pr", "comment", *url, "--body", stage["request"]], cwd=job, capture_output=True, text=True)
        if asked.returncode:
            return f"gh pr comment failed: {(asked.stderr or asked.stdout).strip()}"
        state["requested"] = url[0]
    return None


def write_report(job, task, state, flow):
    """REPORT.md: what the leader tells the owner when the review is done, from the files the stages left."""
    text = lambda name: (job / name).read_text(encoding="utf-8", errors="replace") if (job / name).exists() else ""  # noqa: E731
    url = (text("pr.txt").split() or ["(PR 없음)"])[0]
    review, body = text("review.md"), text("review-body.md")
    # The owner merges the SHA the review names, never one read from repo/ (POLICY §5).
    head = re.search(r"리뷰한 head[^\n]*?\b([0-9a-f]{40})\b", review)
    head = head[1] if head else None
    counts = re.search(r"고친 것:[^\n]*?P0\s*(\d+)\D+P1\s*(\d+)\D+P2\s*(\d+)", review)
    left = re.search(r"남은 지적:[^\n]*", review)
    number = url.rstrip("/").rsplit("/", 1)[-1]
    door = [l.strip() for l in body.splitlines() if re.match(r"\s*(Door|신뢰 경계):", l)]
    easy_pr = state.get("easy_pr") and state.get("easy_pr") == state.get("checked")
    models = "\n".join(f"- {m['stage']}: {m['model']}" + (" (haiku 실패 뒤 다시 돌림)" if m["rerun"] else "") for m in state.get("models", []))
    check = text("check.md").split("```")[0].strip()
    setup = text("setup.md").split("```")[0].strip()
    unverified = [l.strip().removeprefix("- ") for l in (review + "\n" + body).splitlines() if "확인하지 않" in l or "미확인" in l]
    report = (f"# {task['name']} 결과\n\n- PR: {url}\n"
              + (f"- 리뷰한 head: `{head}`\n- 병합: `gh pr merge {number} --squash --match-head-commit {head}`\n" if head
                 else "- 리뷰한 head: review.md에서 40자 SHA를 찾지 못함. 병합 명령을 만들지 않는다\n")
              + "- 리뷰가 고친 지적: " + (f"P0 {counts[1]}, P1 {counts[2]}, P2 {counts[3]}" if counts else "review.md에서 찾지 못함") + "\n"
              + f"- {left[0].strip() if left else '남은 지적: review.md에서 찾지 못함'}\n"
              + "".join(f"- {l}\n" for l in door)
              + ("" if easy_pr else "- 되돌리기 어려움이거나 신뢰 경계를 건드린다(엔진이 쉬움을 확인하지 못했다): 사람이 diff를 읽고 병합한다\n")
              + f"\n## 단계별 모델\n\n{models or '- 없음'}\n\n## 돌린 명령\n\n{setup or '- setup: 없음'}\n\n{check or '- check: 없음'}\n"
              + "\n## 확인하지 않은 것\n\n" + ("".join(f"- {l}\n" for l in unverified) or "- 리뷰 보고와 PR 본문에 적힌 것이 없다\n")
              + f"\n## 리뷰 보고\n\n{review.strip() or '없음'}\n")
    (job / "REPORT.md").write_text(report, encoding="utf-8")


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

    def retry(error):
        """True once per stage for an error a second try may clear."""
        if not any(t in error for t in TRANSIENT) or state.setdefault("retried", {}).get(state["stage"], 0) >= 1:
            return False
        state["retried"][state["stage"]] = 1
        save(state_path, state)
        print(f"retrying once in {RETRY_WAIT:g}s: {error}")
        time.sleep(RETRY_WAIT)
        return True

    if state["stage"] == "pr":  # a task saved before the ready stage replaced the person's Ready
        move("ready")
    blocked, intent = job / "BLOCKED.md", job / "INTENT.md"
    # The leader answers a block in INTENT.md's 결정 기록 and reruns: a newer INTENT.md is that answer.
    # Only here, once per run: a worker that blocks and then writes INTENT.md in the same run must not unblock itself.
    if blocked.exists() and intent.exists() and intent.stat().st_mtime > blocked.stat().st_mtime:
        setup = SETUP_BLOCK in blocked.read_text(encoding="utf-8", errors="replace")
        blocked.replace(job / f"BLOCKED.{int(blocked.stat().st_mtime)}.md")
        state["fails"].clear()
        state.pop("retried", None)  # an answered stage gets its one retry again
        save(state_path, state)
        if setup and (error := run_setup(job, task)):  # an answered setup block runs setup again before anything else
            return block(job, error)

    while True:
        if blocked.exists():
            print(f"blocked: see {blocked}")
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
        elif stage["by"] == "script" and stage.get("body"):
            # Push only the commit the check stage passed: a commit made since (a rerun after a block) goes through it again.
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=job / "repo", capture_output=True, text=True).stdout.strip()
            if not (job / stage["out"]).exists() and head != state.get("checked"):
                check = next((s["id"] for s in reversed(flow[:index]) if s["by"] == "script" and not s.get("body")), None)
                if not check:
                    return block(job, "no check stage before the draft stage: push only a tested commit")
                move(check)
                continue
            raw = read_body(job / stage["body"])
            # The commit compared above, not HEAD again: HEAD can move between here and the push.
            approved = next((state.get("approved", {}).get(s["id"]) for s in flow if stage.get("easy") and s.get("approve") == stage["easy"]), None)
            seen = {}
            if error := open_draft(job, task, stage, raw, state["checked"], approved, seen):
                if retry(error):
                    continue
                return block(job, error)
            if seen:  # the review runs on sonnet only for a PR proven easy here; a PR opened elsewhere gets opus
                state["easy_pr"] = state["checked"]
                save(state_path, state)
        elif stage["by"] == "script" and stage.get("ready"):
            if error := make_ready(job, stage, state):
                if retry(error):
                    continue
                return block(job, error)
            save(state_path, state)
        elif stage["by"] == "script" and stage.get("edit"):
            if error := post_body(job, task, stage):
                if retry(error):
                    continue
                return block(job, error)
            write_report(job, task, state, flow)
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
                state.update(checked=head)  # saved by move() below
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
            argv = claude_argv(config, stage, job, task, state)
            model = argv[argv.index("--model") + 1]
            # A restarted worker re-reads both files: the leader may have answered a block in INTENT.md since its last turn.
            again = any(m["stage"] == stage["id"] for m in state.get("models", [])) or "--resume" in argv
            message = (f"This stage runs again. Re-read {job / 'INTENT.md'} (its 결정 기록 may hold new answers) and {prompt}, "
                       f"then follow {prompt} exactly." if again else f"Read {prompt} and follow it exactly.")
            error = runner(f"{TAG}-{name}-{stage['id']}"[:32], job, argv, message)
            if not isinstance(error, NotStarted):
                if session_key(stage, state) not in state["sessions"]:
                    state["sessions"].append(session_key(stage, state))
                state.setdefault("models", []).append({"stage": stage["id"], "model": model,
                                                       "rerun": stage["id"] in state.get("escalated", [])})
                save(state_path, state)  # a blocked stage returns below; the next run must still --resume
            if error:
                if retry(error):
                    continue
                return block(job, error)
            if (job / "BLOCKED.md").exists():
                if model == "haiku":  # a haiku worker that gave up: sonnet tries once in a new session
                    (job / "BLOCKED.md").replace(job / "BLOCKED.haiku.md")
                    state.setdefault("escalated", []).append(stage["id"])
                    save(state_path, state)
                continue
            if not out.exists():
                failed = f"{stage['id']} ended without writing {out.name}"
        if failed:
            # A failed haiku run (no result file, or a failed check of its commit): sonnet reruns it once, in a new session.
            target = stage.get("retry", stage["id"])
            ran = [m["model"] for m in state.get("models", []) if m["stage"] == target]
            if ran[-1:] == ["haiku"] and target not in state.get("escalated", []):
                state.setdefault("escalated", []).append(target)
            state["fails"][stage["id"]] = state["fails"].get(stage["id"], 0) + 1
            save(state_path, state)
            if state["fails"][stage["id"]] >= 2:
                state["fails"].clear()  # an answer in INTENT.md gets two fresh tries
                save(state_path, state)
                return block(job, f"{failed}, twice in a row")
            move(stage.get("retry", stage["id"]))
            continue
        state["fails"].pop(stage["id"], None)
        state.get("retried", {}).pop(stage["id"], None)
        move(following)


def run_test(job, task, **kwargs):
    """The owner wrote this command (the product's AGENTS.md test cell) with `new`."""
    return subprocess.run(task["test"], shell=True, cwd=job / "repo", **kwargs)


def test(name):
    """The worker's Bash output is cut in the middle, which lost the summary line: the full output goes to test.log."""
    job = JOBS / name
    error = missing(job, name, "task.json")
    if error:
        print(error, file=sys.stderr)
        return 2
    log = job / "test.log"
    with open(log, "w+b") as out:
        code = run_test(job, load(job / "task.json", None), stdout=out, stderr=subprocess.STDOUT).returncode
        out.seek(0)
        text = out.read().decode("utf-8", errors="replace")
    print(text[-4000:])
    print(f"exit {code}; full output: {log}")
    return code


def preflight(name):
    """review_preflight.py reads the git repository of its working folder; a worker's folder is inside the office checkout."""
    job = JOBS / name
    error = missing(job, name, "task.json", "pr.txt")
    if error:
        print(error, file=sys.stderr)
        return 2
    pr = (job / "pr.txt").read_text(encoding="utf-8").split()  # the draft stage or a person wrote it
    script = BASELINE / "tools/review_preflight.py"
    diff_file = job / "pr.diff"
    diff_file.unlink(missing_ok=True)
    result = subprocess.run([sys.executable, str(script), *pr[:1], "--json"], cwd=job / "repo", capture_output=True,
                            text=True, errors="replace")
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode:  # 1 means a secrets file changed: the review must not open the diff
        return result.returncode
    # The engine runs the diff and hands over the text, built from base and head checked here, with .env* left out.
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
    diff = subprocess.run(argv, capture_output=True)
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
    # The base ref preflight printed, nothing else: no options, no `..` revision ranges.
    if error or not re.fullmatch(r"refs/remotes/[\w/-]+(\.[\w-]+)*", base) or "//" in base:
        print(error or f"base must be the preflight's base ref, got {base!r}", file=sys.stderr)
        return 2
    script = BASELINE / "profiles/matt-skills/check_skills.py"
    return subprocess.run([sys.executable, "-I", str(script), base, "--harness", "claude"], cwd=job / "repo").returncode


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


def commands(text):
    """The setup, check and test cells of an AGENTS.md 「명령」 section (POLICY §2), None for N/A, else why not."""
    section = re.search(r"^## 명령[ \t]*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not section:
        return "AGENTS.md has no `## 명령` section"
    cells = {}
    for name in ("setup", "check", "test"):
        found = re.search(rf"^- {name}: `([^`]+)`", section[1], re.M)
        if not found:
            return f"AGENTS.md 「명령」 has no `- {name}: `<command>`` line"
        cells[name] = None if found[1].startswith("N/A") else found[1]
    if not cells["test"]:
        return "the AGENTS.md test cell is N/A: the pipeline needs a test command"
    return cells


def new(name, repo, base):
    # The name becomes a folder, a branch and an hc terminal name (a-z 0-9 - _ only).
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,23}", name):
        print("task name: a-z 0-9 - _, at most 24 characters", file=sys.stderr)
        return 2
    if base.startswith("origin/"):  # read the commands and branch from what origin has now, not a stale local ref
        subprocess.run(["git", "-C", repo, "fetch", "-q", "origin", base.removeprefix("origin/")], capture_output=True)
    shown = subprocess.run(["git", "-C", repo, "show", f"{base}:AGENTS.md"], capture_output=True, text=True)
    cells = commands(shown.stdout) if shown.returncode == 0 else f"no AGENTS.md at {base}"
    if isinstance(cells, str):
        print(f"{cells}: run engine.py doctor {repo}", file=sys.stderr)
        return 2
    job = JOBS / name
    job.mkdir(parents=True)
    # Each cell in its own subshell: a test cell like `cd app; make test` must not hide a failing check.
    # The newlines keep a trailing `# comment` in a cell from swallowing the closing parenthesis.
    test = " && ".join(f"(\n{c}\n)" for c in (cells["check"], cells["test"]) if c)
    save(job / "task.json", {"name": name, "repo": str(Path(repo).resolve()), "test": test, "setup": cells["setup"], "base": base})
    (job / "INTENT.md").write_text(
        f"# {name}\n\n## 목표\n\n## 지시 원문\n\n## 하지 않을 것\n\n## 결정 기록\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "worktree", "add", "-b", f"office/{name}", str(job / "repo"), base], check=True)
    if error := run_setup(job, load(job / "task.json", None)):
        return block(job, error)
    print(job / "INTENT.md")
    return 0


SETUP_BLOCK = "setup has not passed in repo/"


def run_setup(job, task):
    """Run the setup cell once in repo/, else why not. BLOCKED.md stands while it runs: an interrupted setup stays blocked."""
    if not task.get("setup"):
        return None
    (job / "BLOCKED.md").write_text(f"# 막힘\n\n{SETUP_BLOCK}: `{task['setup']}` is running or was interrupted\n", encoding="utf-8")  # not block(): no "blocked:" line while setup runs
    done = subprocess.run(task["setup"], shell=True, cwd=job / "repo", capture_output=True, text=True)
    (job / "setup.md").write_text(f"# setup\n\n- 명령: `{task['setup']}`\n- exit {done.returncode}\n\n```\n"
                                  f"{(done.stdout + done.stderr)[-4000:]}\n```\n", encoding="utf-8")
    if done.returncode:
        return (f"{SETUP_BLOCK}: `{task['setup']}` exit {done.returncode}. Fix the product or the machine, write the answer "
                f"in INTENT.md's 결정 기록, and run again: the engine runs setup again first. Output: {job / 'setup.md'}")
    (job / "BLOCKED.md").unlink()
    return None


def doctor(repo):
    """What a product repository lacks to connect, checked on origin/main. Exit 0 when nothing is missing.
    Every fix is in the product repository or this machine: the pipeline does not bend to one product."""
    repo, missing_ = Path(repo).resolve(), []

    def report(ok, what, fix=""):
        print(("ok   " if ok else "FAIL ") + what + ("" if ok else f": {fix}"))
        if not ok:
            missing_.append(what)

    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)  # noqa: E731
    report(git("fetch", "-q", "origin", "main").returncode == 0, "origin has a main branch", "push main to origin")
    shown = git("show", "origin/main:AGENTS.md")
    cells = commands(shown.stdout) if shown.returncode == 0 else "no AGENTS.md on origin/main"
    report(not isinstance(cells, str), "AGENTS.md 「명령」 has setup, check and test cells",
           f"{cells} (repo-baseline templates/core/AGENTS.md)")
    report(git("cat-file", "-e", "origin/main:.github/pull_request_template.md").returncode == 0, "PR template on main",
           "add .github/pull_request_template.md (repo-baseline templates/core/pull_request_template.md)")
    authed = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
    report(authed, "gh is logged in", "run gh auth login")
    repo_slug = slug(git("config", "--get", "remote.origin.url").stdout)  # as written, like the draft stage
    protected = False
    if repo_slug and authed:
        api = lambda path, *a: subprocess.run(["gh", "api", path, *a], capture_output=True, text=True)  # noqa: E731
        rules = api(f"repos/{repo_slug}/rules/branches/main", "--jq", "length")
        protected = api(f"repos/{repo_slug}/branches/main/protection").returncode == 0 or (
            rules.returncode == 0 and rules.stdout.strip() not in ("", "0"))
    report(protected, "main is protected (branch protection or a ruleset)",
           "turn it on (repo-baseline tools/apply_github_settings.py)" if repo_slug else "origin is not a GitHub repository")
    if isinstance(cells, str):
        return 1
    root = Path(tempfile.mkdtemp(prefix="office-doctor-"))
    checkout = root / "main"
    try:
        added = git("worktree", "add", "-q", "--detach", str(checkout), "origin/main")
        report(added.returncode == 0, "a fresh checkout of origin/main", added.stderr.strip())
        for name in () if added.returncode else ("setup", "check", "test"):  # setup first: check and test may need what it installs
            if cells[name] is None:
                print(f"ok   {name}: N/A")
                continue
            done = subprocess.run(cells[name], shell=True, cwd=checkout, capture_output=True, text=True)
            report(done.returncode == 0, f"{name} passes on main: `{cells[name]}`",
                   f"exit {done.returncode}; make it pass on a fresh checkout of main\n{(done.stdout + done.stderr)[-1500:]}")
    finally:
        git("worktree", "remove", "--force", str(checkout))
        shutil.rmtree(root, ignore_errors=True)
    return 1 if missing_ else 0


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
    created.add_argument("--base", default="main")
    sub.add_parser("doctor").add_argument("repo")
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
        return new(args.task, args.repo, args.base)
    if args.command == "doctor":
        return doctor(args.repo)
    if args.command == "workers":
        return workers()
    if args.command == "skills":
        return skills(args.task, args.base)
    return {"run": run, "approve": approve, "test": test, "preflight": preflight}[args.command](args.task)


if __name__ == "__main__":
    sys.exit(main())
