# repo-office

이슈나 개발 요청 하나를 받아 리뷰까지 마친 PR로 끌고 가는 실험 파이프라인이다. 사람은 설계 승인과 병합만 한다. 나머지는 상주 세션 `leader/`와 엔진(`pipeline/engine.py`)이 단계마다 작업자를 띄워 돌린다. 규칙은 [AGENTS.md](AGENTS.md), 이어받는 사람이 읽을 기록은 [HANDOFF.md](HANDOFF.md), 제품 저장소 연결은 [docs/connect.md](docs/connect.md)에 있다.

```text
사람 ─ leader ─ engine.py run ─┬─ 설계(opus) ─ 사람 승인 ─ 구현(haiku 또는 sonnet) ─ 검사
                               └─ push·PR·Ready ─ 리뷰(다른 세션) ─ 본문·REPORT.md ─ 사람 병합
```

## 필요한 것

macOS에서만 돌려 봤다.

| 무엇 | 왜 | 설치 |
| --- | --- | --- |
| Python 3.9 이상 | 엔진. 표준 라이브러리만 쓴다 | macOS의 `/usr/bin/python3`(3.9)로 충분하다 |
| git, [gh](https://cli.github.com) | worktree, push, PR | `brew install gh` 뒤 `gh auth login` |
| [Claude Code](https://claude.com/claude-code) | leader와 작업자 | 설치하고 로그인한다 |
| hc(herdr-core) | 작업자를 대화형 Claude Code로 띄우고, 턴이 끝날 때까지 기다리고, 닫는 터미널 도구 | `curl -fsSL https://releases.herdr.dev/core/install.sh \| sh`(`~/.local/bin`에 `hc`가 생긴다). 판 올리기는 `hc update` |
| [repo-baseline](https://github.com/minjunkim-dev/repo-baseline) 클론 | 리뷰 지시서, 리뷰 전 확인 스크립트, 정책 | `git clone` |
| mattpocock-skills 플러그인 | 작업자가 부르는 `tdd`·`diagnosing-bugs`·`code-review` 스킬 | Claude Code에서 `/plugin marketplace add mattpocock/skills`, 그다음 `/plugin install mattpocock-skills@mattpocock`. 사용자 설정에서는 꺼 둬도 된다. office `.claude/settings.json`이 작업자에게만 켠다 |

## 처음 한 번

1. office 체크아웃 루트에 `baseline.path`를 만들고 repo-baseline 클론의 절대 경로를 한 줄로 적는다. git은 이 파일을 무시한다.
2. office 체크아웃에서 Claude Code를 한 번 열어 폴더를 신뢰한다. 작업자는 이 체크아웃 안 `jobs/<과제>/`에서 뜬다. 신뢰하지 않은 폴더면 엔진이 `BLOCKED.md`를 쓴다.
3. Claude Code 밖의 깨끗한 셸에서 `hc server stop`, 그다음 `hc server start`를 돌린다. Claude Code 안에서 hc를 처음 띄우면 작업자가 그 세션의 환경 변수를 물려받아 턴 끝을 알리지 못한다(HANDOFF §6).
4. 제품 저장소를 [docs/connect.md](docs/connect.md)의 네 단계로 연결한다. 마지막에 `python3 pipeline/engine.py doctor <제품 저장소>`가 통과해야 한다.
5. `cd leader && claude`로 leader를 띄우고 할 일을 말한다.

제품 저장소가 mise를 쓰면 과제마다 사람이 `mise trust jobs/<과제>/repo/.mise.toml`을 한 번 한다.

## 진행 보기

- `python3 pipeline/engine.py workers`: 과제마다 단계, 경과, 모델, PR 또는 막힌 이유를 표로 보여 준다.
- `jobs/board.html`: 같은 내용을 픽셀 사무실로 그린 페이지다. 과제마다 작업자가 지금 단계의 책상에 앉고, 막히면 「!」, 사람 차례면 「?」 말풍선을 띄운다. 아래 카드에 단계 진행, 모델, PR 또는 막힌 이유가 있다. `engine.py run`이 단계를 옮길 때마다 다시 쓰고, 페이지는 5초마다 다시 읽는다. `python3 pipeline/engine.py board`로 바로 만들 수도 있다. 파일만 읽으므로 토큰을 쓰지 않는다.
- 작업자 화면은 `hc ls`로 이름을 찾고 `hc attach <이름>`으로 본다(`ctrl+\`로 빠진다). 작업자는 hc 터미널이라 Claude 데스크탑 앱의 세션 목록에는 나오지 않는다.

작업자가 사용량 한도에 걸리면 엔진은 그 메시지(예: `resets 12:10am`)를 `BLOCKED.md`에 적고 멈춘다. 한 턴이 4시간(`OFFICE_WORKER_TIMEOUT`, 초)을 넘어도 작업자를 닫고 멈춘다.
