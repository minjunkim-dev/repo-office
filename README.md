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

`jobs/board.html`은 한 화면짜리 상태판이다. 오른쪽(`jobs/board-tasks.html`)이 파이프라인이고, 왼쪽은 사무실 그림이다. 900px 이하에서는 파이프라인이 위, 사무실이 아래에 온다.

- 맨 위 줄은 과제 수와 상태별 개수, 데이터 시각이다.
- 단계 칸(설계 → 설계 승인 → 구현 → 검사 → PR·리뷰 → 병합 대기)은 과제가 지금 어느 단계에 있는지 보인다.
- 과제 카드는 지금 무엇을 하는지(「sonnet 작업자가 구현 중 (12분째)」)와 사람이 할 일(승인 명령, 막힌 이유)을 쓴다. 그 아래 단계 줄은 끝난 단계마다 실제로 돈 모델을 보인다(예: `구현 · haiku→sonnet`). 그래서 건너뛴 단계나 다시 돈 단계가 보인다. 작업자가 일하는 동안에는 `hc attach` 이름도 나온다.
- 초록은 진행 중, 노랑은 사람 차례, 파랑은 병합 대기, 회색은 멈춤, 빨강은 막힘이나 `state.json` 읽기 실패다.
- 진행 중은 `engine.py run`이 그 과제의 `run.lock`을 쥐고 있을 때만 나온다. 엔진이 없거나 죽었으면 같은 단계라도 회색 멈춤과 `engine.py run <과제>`가 나온다.

`engine.py run`은 단계를 옮길 때와, 도는 동안 5초마다(`OFFICE_BOARD_EVERY`) 상태판을 다시 쓴다. 화면은 5초마다 다시 읽는다. 그래서 경과 시간과 마지막 파일이 몇 초 안에 따라온다. 데이터가 20초보다 오래되면 화면은 도는 엔진이 없다고 보고 진행 중을 멈춤으로 바꾼다. 오른쪽만 다시 읽으므로 사무실 움직임은 끊기지 않는다. `python3 pipeline/engine.py board`로 바로 만들 수도 있다. 상태판은 파일만 읽으므로 토큰을 쓰지 않는다. 파일로 열었는데 칸이 비면 `python3 -m http.server 8765 --bind 127.0.0.1 --directory jobs`로 띄우고 `http://127.0.0.1:8765/board.html`을 연다.

사무실 그림에는 이 파이프라인의 과제만 나온다. 방 여섯 개가 단계 순서(① 설계 → ⑥ 병합 대기)로 놓이고, 과제 하나가 캐릭터 하나다. 캐릭터 머리 위에는 두 줄만 쓴다. 첫 줄은 과제 이름 그대로, 둘째 줄은 단계·모델·상태다(예: `구현 · sonnet · 진행 중`). 상태 색은 카드와 같고, 사람이 볼 상태(사람 차례·병합 대기·멈춤·막힘)에는 머리 옆에 표시가 붙는다. 진행 중이면 책상에서 타자를 치고(작업자) 또는 서류를 읽고(엔진 검사), 단계가 바뀌면 다음 방으로 걸어간다. 사람이 할 방(설계 승인·병합 대기)에서는 화이트보드 앞에 서 있다. 한 방에는 두 명까지 보이고, 더 있으면 방 이름 옆에 안 보이는 수를 쓴다. 상태는 오른쪽 카드가 계산한 것을 그대로 받으므로 두 쪽이 어긋나지 않는다. 브라우저는 숨은 탭에서 그림을 멈추므로, 걷는 모습은 화면을 볼 때만 움직인다.

그림은 [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents) 1.5.0의 캐릭터·가구 그림(MIT, 캐릭터는 JIK-A-4의 CC0 그림 기반)을 `pipeline/office/`에 그대로 두고, 출처와 라이선스는 `pipeline/office/NOTICE`에 있다. `engine.py board`가 이 그림을 상태판 파일 안에 넣으므로, 상태판은 다른 곳에서 아무것도 불러오지 않는다. Pixel Agents 프로그램은 더 쓰지 않는다. 그 프로그램은 이 기기의 Claude 세션을 모두 보여 줘서 office 작업자만 고를 수 없었다.

작업자 화면을 직접 보려면 `hc ls`로 이름을 찾고 `hc attach <이름>`을 돌린다. 빠져나올 때는 `ctrl+\`를 누른다. 작업자는 hc 터미널에서 돌므로 Claude 데스크탑 앱의 세션 목록에는 나오지 않는다.

작업자가 사용량 한도에 걸리면 엔진이 그 메시지(예: `resets 12:10am`)를 `BLOCKED.md`에 적고 멈춘다. 한 턴이 4시간(`OFFICE_WORKER_TIMEOUT`, 초 단위)을 넘어도 엔진이 작업자를 닫고 멈춘다.
