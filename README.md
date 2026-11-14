# repo-office

repo-office는 이슈나 개발 요청 하나를 PR까지 만드는 실험 파이프라인이다. PR은 리뷰까지 마친 상태로 나온다. 사람은 설계를 승인하고 PR을 병합하기만 한다.

나머지는 두 가지가 돌린다. leader는 사람과 말하고 과제를 등록하는 Claude Code 세션이다(`leader/`). 늘 떠 있는 세션은 leader 하나뿐이다. 엔진(`pipeline/engine.py`)은 설계·구현·리뷰 같은 단계마다 Claude Code를 띄우고, 단계가 끝나면 닫는다. 이렇게 단계마다 띄우는 Claude Code를 작업자라고 부른다. 구현 작업자는 설계 작업자의 세션을 이어받아 설계 맥락을 잇는다. 리뷰 작업자는 다른 세션이다.

규칙은 [AGENTS.md](AGENTS.md)에 있다. 이어받는 사람이 읽을 기록은 [HANDOFF.md](HANDOFF.md)에 있다. 제품 저장소를 연결하는 법은 [docs/connect.md](docs/connect.md)에 있다.

```text
사람 ─ leader ─ engine.py run ─┬─ 설계(opus) ─ 사람 승인 ─ 구현(haiku 또는 sonnet) ─ 검사
                               └─ push·PR·Ready ─ 리뷰(다른 세션) ─ 본문·REPORT.md ─ 사람 병합
```

## 필요한 것

macOS에서만 돌려 봤다.

| 무엇 | 왜 | 설치 |
| --- | --- | --- |
| Python 3.9 이상 | 엔진을 돌린다. 표준 라이브러리만 쓴다 | macOS의 `/usr/bin/python3`(3.9)로 충분하다 |
| git, [gh](https://cli.github.com) | worktree를 만들고, push하고, PR을 연다 | `brew install gh` 뒤 `gh auth login` |
| [Claude Code](https://claude.com/claude-code) | leader와 작업자가 이것으로 돈다 | 설치하고 로그인한다 |
| hc(herdr-core) | 터미널 도구다. 작업자를 대화형 Claude Code로 띄우고, 턴이 끝날 때까지 기다리고, 닫는다 | `curl -fsSL https://releases.herdr.dev/core/install.sh \| sh`(`~/.local/bin`에 `hc`가 생긴다). 판 올리기는 `hc update` |
| [repo-baseline](https://github.com/minjunkim-dev/repo-baseline) 클론 | 리뷰 지시서, 리뷰 전에 돌리는 확인 스크립트, 정책이 있다 | `git clone` |
| mattpocock-skills 플러그인 | 작업자가 `tdd`·`diagnosing-bugs`·`code-review` 스킬을 부른다 | Claude Code에서 `/plugin marketplace add mattpocock/skills`, 그다음 `/plugin install mattpocock-skills@mattpocock`. 사용자 설정에서는 꺼 둬도 된다. office `.claude/settings.json`이 작업자에게만 켠다 |

worktree는 한 git 저장소의 브랜치를 다른 폴더에 따로 꺼낸 작업 폴더다. 엔진은 과제마다 제품 저장소의 worktree를 만들어, 여러 과제가 서로 건드리지 않고 함께 돈다.

## 처음 한 번

1. office 체크아웃 루트에 `baseline.path` 파일을 만든다. 그 안에 repo-baseline 클론의 절대 경로를 한 줄로 적는다. git은 이 파일을 무시한다.
2. office 체크아웃에서 Claude Code를 한 번 열고 폴더를 신뢰한다. 작업자는 이 체크아웃 안의 `jobs/<과제>/`에서 뜬다. 폴더를 신뢰하지 않았으면 엔진이 `BLOCKED.md`를 쓰고 멈춘다.
3. Claude Code 밖의 깨끗한 셸에서 `hc server stop`, 그다음 `hc server start`를 돌린다. Claude Code 안에서 hc 서버를 처음 띄우면 작업자가 그 세션의 환경 변수를 물려받는다. 그러면 작업자가 턴이 끝났다고 알리지 못한다(HANDOFF §6).
4. 제품 저장소를 [docs/connect.md](docs/connect.md)의 네 단계로 연결한다. 마지막 확인으로 `python3 pipeline/engine.py doctor <제품 저장소>`가 통과해야 한다.
5. `cd leader && claude`로 leader를 띄우고 할 일을 말한다.

제품 저장소가 mise를 쓰면 과제마다 사람이 `mise trust jobs/<과제>/repo/.mise.toml`을 한 번 돌린다.

## 진행 보기

진행 상황은 네 곳에서 볼 수 있다.

`python3 pipeline/engine.py workers`는 과제마다 단계, 경과 시간, 모델, PR 또는 막힌 이유를 표로 보여 준다.

`jobs/board.html`은 한 화면짜리 상태판이다. 왼쪽에는 아래의 도트 사무실(Pixel Agents)이 움직인다. 오른쪽에는 단계 칸(설계 → 설계 승인 → 구현 → 검사 → PR·리뷰 → 병합 대기)과 과제 카드가 있다. 카드에는 진행 막대, 모델, PR 또는 막힌 이유가 나온다. 초록은 작업 중, 노랑은 사람 차례, 빨강은 막힘이나 `state.json` 읽기 실패다. 오른쪽(`jobs/board-tasks.html`)만 5초마다 다시 읽으므로 사무실 움직임은 끊기지 않는다. 창 너비가 800px쯤으로 좁으면 왼쪽 사무실 칸이 잘린다. 사무실 칸의 +/- 버튼으로 줄이면 전체가 보인다.

상태판은 `engine.py run`이 단계를 옮길 때마다 다시 쓴다. `python3 pipeline/engine.py board`로 바로 만들 수도 있다. 상태판은 파일만 읽으므로 토큰을 쓰지 않는다. 파일로 열었는데 칸이 비면 `python3 -m http.server 8765 --bind 127.0.0.1 --directory jobs`로 띄우고 `http://127.0.0.1:8765/board.html`을 연다.

도트 사무실은 [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)(MIT)다. 이 기기의 Claude Code 세션마다 도트 캐릭터를 책상에 앉힌다. 세션이 쓰는지, 읽는지, 기다리는지를 움직임과 말풍선으로 보인다. 띄우는 법은 다음과 같다.

1. `npx pixel-agents@1.5.0 --port 3100 --no-terminal`로 띄운다.
2. 처음 한 번은 출력된 주소를 열고 Settings에서 **Watch All Sessions**를 켠다. 작업자는 과제 폴더마다 따로 뜨므로, 이 설정을 켜야 작업자가 보인다. 출력된 주소에는 `?token=`이 붙어 있다. 이 토큰이 있으면 누구나 설정을 바꾸고 훅을 설치할 수 있으니 남에게 보이지 않게 한다.
3. 훅 설치 안내(Install Hooks)가 나오면 「Don't Ask Again」으로 넘긴다. 훅은 `~/.claude/settings.json`에 들어가 모든 세션에 붙는다. 하지만 작업자는 `--setting-sources project`로 돌아서 이 훅을 읽지 않는다. Pixel Agents는 훅이 없어도 `~/.claude/projects/`의 대화 기록에서 상태를 읽는다.

상태판에는 토큰 없는 주소만 넣는다. 기본값은 `http://127.0.0.1:3100`이다. 다른 주소를 쓰면 `OFFICE_PIXEL_AGENTS`에 적는다. 2026-11-15에 pixel-agents@1.5.0에서 Watch All Sessions를 켜고 hc 작업자가 보이는지 확인했다. `engine.py run`으로 띄운 설계 작업자가 캐릭터로 나왔다. 캐릭터 이름은 과제 폴더 이름의 끝부분이다(예: `viz-trial`이면 `trial`). 작업자가 `design.prompt.md`·`INTENT.md`를 읽고 `design.md`를 쓰는 도구 호출도 보였다. 엔진이 작업자를 닫으면 캐릭터도 사라진다. 같은 때 `jobs/board.html`에서는 그 과제 카드가 설계(초록)에서 설계 승인(노랑)으로 옮겨졌다.

작업자 화면을 직접 보려면 `hc ls`로 이름을 찾고 `hc attach <이름>`을 돌린다. 빠져나올 때는 `ctrl+\`를 누른다. 작업자는 hc 터미널에서 돌므로 Claude 데스크탑 앱의 세션 목록에는 나오지 않는다.

작업자가 사용량 한도에 걸리면 엔진이 그 메시지(예: `resets 12:10am`)를 `BLOCKED.md`에 적고 멈춘다. 한 턴이 4시간(`OFFICE_WORKER_TIMEOUT`, 초 단위)을 넘어도 엔진이 작업자를 닫고 멈춘다.
