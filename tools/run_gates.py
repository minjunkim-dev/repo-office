#!/usr/bin/env python3
"""Run the baseline checks named by the office gates and write the task report."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "claude-office/worker-01/tasks/office-smoke/REPORT.md"
COMMANDS = (
    [sys.executable, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"],
    [sys.executable, "-m", "unittest", "discover", "-s", "tools", "-p", "test_*.py"],
)


def main():
    lines = ["# office-smoke 관문 보고", ""]
    failed = False
    for command in COMMANDS:
        result = subprocess.run(command, cwd=ROOT)
        shown = " ".join(f"'{part}'" if part == "test_*.py" else part for part in command[2:])
        lines.append(f"- 테스트: `python3 -m {shown}` exit {result.returncode}")
        failed = failed or result.returncode != 0
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs_ok = "repo-baseline-office" in readme and "claude-office/" in readme
    failed = failed or not docs_ok
    lines.extend([
        "- 린트: 비해당. 이 저장소는 포맷터나 린트 명령을 두지 않는다.",
        "- 화면: 비해당. 이 과제는 사용자에게 보이는 화면을 바꾸지 않는다.",
        f"- 문서: README 실험 안내 {'확인' if docs_ok else '없음'}",
        "- 보고: 이 파일이다. 서버 배포와 production 변경은 실행하지 않았다.",
        "- 미확인: 별도 세션의 Standards·Spec 리뷰와 원격 CI 결과는 이 로컬 실행에 포함되지 않는다.",
        "",
    ])
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(REPORT)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
