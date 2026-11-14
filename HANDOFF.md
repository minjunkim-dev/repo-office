# 이어받기

다른 사람·계정·에이전트가 이 저장소 작업을 바로 이어받기 위한 기록이다. 목표, 구성 요소, 스킬 연결, 정한 것, 엔진의 함정을 적는다. 규칙은 [AGENTS.md](AGENTS.md)와 repo-baseline `POLICY.md`가 정본이고 여기에 다시 적지 않는다. 2026-11-12에 옛 `HARNESS.md`를 이 문서에 합쳤다. 옛 경위(재설계 전 Matt 스파인, `claude-office/` 시절의 원본 맞추기)는 git 기록에 있다.

## 1. 목표와 지금 상태

소유자가 2026-11-11에 정한 목표:

- 소유자가 이슈나 개발 요청 하나를 던지면, 정해진 파이프라인이 그것을 리뷰를 마친 PR까지 끌고 간다. 병합은 사람이 한다.
- 모델이 부를 수 있는 스킬(`tdd`, `diagnosing-bugs`, `codebase-design`, `code-review`)은 단계마다 작업자가 부른다. 사람만 부를 수 있는 스킬(Matt 뼈대, pstack 대부분, §3)은 알맞은 때에 leader가 사람에게 실행을 권한다.
- 기계로 막을 수 있는 규칙(push·병합 금지, 완료 증거)은 지시가 아니라 거부 규칙·엔진·CI로 지킨다. 판단이 필요한 것(설계 승인, 병합)은 사람이 한다. 2026-11-14부터 거부 규칙은 push·`gh pr merge`·`gh pr edit`·`gh api` 쓰기만 막고, 작업자에게 Bash가 열려 있어 `bash -c` 같은 우회는 막지 못한다. 그 밖의 것은 지시로만 지킨다(§6).
- repo-baseline 규칙이 바닥이다. office, 스킬 묶음, 개인 도구는 그 위의 선택 층이고, 하나가 빠져도 나머지가 돈다.

지금 상태(2026-11-12):

- 흐름: 접수(leader) → 설계 → 사람 승인(`design.md` sha256) → 구현 → 검사(test 명령, 커밋하지 않은 변경) → 엔진이 push하고 PR을 열어 Ready, 쉬움이 아니면 리뷰 봇 요청 → 리뷰(다른 세션) → 엔진이 리뷰 커밋을 push하고 리뷰 본문을 PR에 올리고 REPORT.md → 사람이 병합.
- 이 흐름 전체를 장난감 저장소에서 엔진과 실제 작업자로 끝까지 돌렸다(leader 접수는 따로 돌렸다)(§6의 2026-11-12 항목). 단위 테스트는 58개다(2026-11-14).
- 열린 이슈는 없다. 남은 결정은 §4에 있다.

## 2. 구성 요소

| 구성 요소 | 어디 | 맡는 층 | 지금 상태 |
| --- | --- | --- | --- |
| repo-baseline(원본) | `minjunkim-dev/repo-baseline`, 로컬 클론 경로는 `baseline.path` | PR이 `main`에 들어가는 조건: 완료 증거, 다른 컨텍스트 리뷰, Door, 사람 병합, GitHub 설정, 회고 | 코어 4파일 + 선택 프로필. 2026-11-12 `main` = `f6566c4` |
| repo-office(이 저장소) | `minjunkim-dev/repo-office`(공개). 2026-11-11에 repo-baseline-office의 `claude-office/`에서 git 기록째 옮겼다 | PR 전 작업을 단계로 돌리고, 초안 PR을 열고, 리뷰를 한 번 돌린다 | 엔진·스킬 연결·초안 PR까지 실측함 |
| Matt Pocock 스킬 | 플러그인 `mattpocock-skills@mattpocock`(캐시 1.3.2). 사용자 설정에서는 꺼져 있고, office `.claude/settings.json`의 `enabledPlugins`가 작업자에게만 켠다 | 리뷰 두 축, TDD, 버그 진단, 설계([matt-skills 프로필](https://github.com/minjunkim-dev/repo-baseline/blob/main/profiles/matt-skills/README.md)) | 작업자에 실린다(§6) |
| pstack | 플러그인 `pstack@cursor-plugins` 0.15.36. 사용자 설정에서 켜져 있고 office 설정에는 없다 | 설계 질문(`interrogate`), 영향 범위(`blast-radius`) | 사람이 부를 때만([pstack 프로필](https://github.com/minjunkim-dev/repo-baseline/blob/main/profiles/pstack/README.md)). leader가 권한다 |
| 개인 도구 | `hc`(herdr-core), ai-memory, trail 등 | 작업자 띄우기, 세션 간 기억 | 엔진이 `hc`를 쓴다 |
| 장난감 저장소 | `minjunkim-dev/office-toy`(공개) | 실측용 제품 저장소 | 시험이 끝나면 지운다 |
| 소비 레포 | BGLike(Godot), smallnext 등 | 실제 과제 | office로 돌린 기록이 있다(§6의 BGLike·smallnext 항목) |

## 3. 스킬 호출의 사실과 단계 배치

자동 호출과 수동 호출은 스킬 작성자가 frontmatter로 정한다. `disable-model-invocation: true`인 스킬은 사람이 이름을 쳐야만 돈다. 모델도, 다른 스킬도 부를 수 없다([skills](https://code.claude.com/docs/en/skills) 「Control who invokes a skill」, 2026-11-11 확인).

| 묶음 | 모델이 부를 수 있음 | 사람만 부름 |
| --- | --- | --- |
| Matt 1.3.2 | `code-review`, `tdd`, `diagnosing-bugs`, `grilling`, `research`, `prototype`, `domain-modeling`, `codebase-design`, `pr`, `writing-for-agents` 등 | `ask-matt`, `grill-with-docs`, `grill-me`, `to-spec`, `to-tickets`, `implement`, `triage`, `retro` 등 |
| pstack 0.15.36 | `setup-pstack` 하나 | 나머지 전부(`interrogate`, `blast-radius`, `correct` …) |

단계 배치(지시서와 leader 규칙에 들어 있다):

| 단계 | 스킬 | 누가 부르나 |
| --- | --- | --- |
| 접수 | `grill-with-docs`, `ask-matt` | leader가 등록 전에 사람에게 권한다 |
| design | `codebase-design`(인터페이스를 정해야 할 때) | 작업자 |
| approve | `blast-radius`, Door가 어려움이면 `interrogate` | leader가 사람에게 권한다 |
| implement | `tdd`, 버그면 먼저 `diagnosing-bugs` | 작업자 |
| review | `code-review`(제품 `REVIEW.md`에 「스킬 위임」 절이 있을 때) | 리뷰 작업자 |
| 병합 뒤 | `retro` | leader가 사람에게 권한다 |

- 작업자는 이름을 앞 이름까지 다 써서 부른다(`mattpocock-skills:code-review`). 앞 이름 없는 `code-review`는 Claude Code 내장 스킬로 간다.
- `Skill` 도구는 허용 목록과 관계없이 돈다. 그래서 단계마다 스킬을 막지는 못한다. 막으려면 `Skill(...)` 거부 규칙이 필요하다.
- 배치하지 않은 후보: 설계의 `domain-modeling`·`research`·`prototype`·`grilling`, 접수의 `triage`·`to-spec`·`to-tickets`, PR의 Matt `pr`(PR 양식이 먼저다), 검사의 pstack `create-verification-skill`, 회고의 pstack `correct`. 필요해지면 위 표에 넣는다.
- Matt `implement`는 같은 세션에서 `code-review`를 불러 POLICY §3(다른 컨텍스트 리뷰)과 부딪히므로 파이프라인에서 쓰지 않는다. pstack `tdd`는 Matt `tdd`와 겹쳐 쓰지 않는다.

## 4. 정한 것과 남은 결정

정한 것(날짜는 소유자가 정한 날):

- 2026-11-11: 사람만 부르는 스킬은 leader가 사람에게 권한다. 엔진이 `hc send`로 `/스킬`을 입력하거나 작업자가 `SKILL.md`를 읽는 것은 작성자의 수동 표시를 우회하므로 하지 않는다.
- 2026-11-11: office는 별도 저장소로 두고, repo-baseline 클론 경로는 `baseline.path`로 받는다. 원본이 정본이다. office를 원본의 프로필로 넣지 않는다(macOS·Claude Code·`hc`에 묶인 코드가 중립 정본에 들어오므로).
- 2026-11-12: 스킬 연결은 office 안(단계표·지시서·leader 규칙)에 둔다. 작업자에는 office `.claude/settings.json`의 `enabledPlugins`로 플러그인을 싣는다. `--setting-sources`를 넓히지 않는다(사용자 전역 허용 규칙이 샌다).
- 2026-11-12: 플러그인 판은 고정하지 않고 기록만 한다. 리뷰 보고 「확인한 것」의 `check_skills.py` 경로·sha256이 그 기록이다.
- 2026-11-12: 검사가 통과하면 엔진이 초안 PR을 연다. 사람이 Ready로 바꾸기 전에는 리뷰가 돌지 않는다. 2026-11-14에 바꿨다: 엔진이 늘 Ready로 바꾼다.
- 2026-11-14: office는 계속 쓴다. 계속 쓸지 판단하는 기준은 두지 않는다.
- 2026-11-14: 엔진은 쉬움이 아닌 PR에만 리뷰 봇을 한 번 요청한다(POLICY §3, repo-baseline #130). 원본은 봇이 건너뛰었을 때만 요청하지만, Ready 직후에는 봇이 아직 돌지 않았을 수 있어 건너뜀을 기다리지 않는다.

남은 결정:

- repo-baseline #130의 규칙 중 둘에 office의 주인이 없다.
  - 리뷰어가 Door를 어려움으로 올렸으면 리뷰어가 봇에 요청한다. 엔진은 쉬움으로 판정한 PR에 요청하지 않았고, 리뷰 지시서도 이 요청을 시키지 않는다.
  - 같은 PR에 다른 모델 패밀리 리뷰(Codex, Cursor agent)를 권장한다. 엔진의 리뷰 단계는 Claude 하나다. 리뷰 작업자는 원본 `prompts/review.md`대로 받지 못한 사실을 「남은 지적」에 적는다.
  - 둘 다 엔진·단계표·지시서를 바꾸는 일이라 소유자 승인과 사람 병합이 필요하다.

## 5. 왜 이렇게 지었나

참고한 두 영상(Solostack, Claude Code 팀 운영)의 줄거리는 다음과 같다.

- 1편(2026-10-24): 역할마다 폴더와 세션을 둔다. `INTENT.md`를 쓰고 설계를 승인받은 뒤 구현한다. 다섯 관문을 거치고 MEMORY→HANDBOOK으로 규칙을 올린다. 위험한 작업은 도구 권한으로 막는다.
- 2편(2026-11-05): 상주 세션은 대장 하나만 둔다. 작업자는 단계표(`stages.json`)대로 그때그때 띄운다. 과제마다 세션 ID 하나로 앞 단계를 이어 써서 캐시를 쓴다. 단계마다 허용 명령을 따로 주고, 승인은 설계 파일 내용에 묶는다. 반복 실패하면 멈추고, `workers` 명령으로 현황을 본다.

원본 repo-baseline은 PR이 `main`에 들어가는 조건을 정한다(완료 증거, 다른 컨텍스트 리뷰, Door, 병합). 영상은 PR 전의 작업 조율을 다룬다. 층이 달라 서로 보완한다. 부딪히는 곳은 모두 원본 쪽으로 정했다.

| 영상 | 원본과의 관계 | 정한 것 |
| --- | --- | --- |
| 다섯 관문, MEMORY→HANDBOOK, reviewer 규칙 | POLICY §2·§6, `prompts/review.md`와 겹침 | 지우고 원본을 따른다 |
| 자체 검수가 구현 세션을 이어 씀 | §3: 물려받은 대화는 다른 컨텍스트가 아님 | 리뷰는 다른 세션 키 |
| 리더가 마지막 판정 | §5: 소유자가 병합 | 엔진·작업자는 병합·push to main 안 함 |
| `CLAUDE.md` | 원본은 `AGENTS.md` 하나 | `AGENTS.md`만 |
| `claude -p`(일회성) | 사용자 전역 규칙이 `-p`를 금지하고 `hc`를 권함 | `hc run --bg`로 대화형 claude |
| 리뷰 반복 3회 초과 시 멈춤 | §3 최대 3라운드 | 엔진은 리뷰를 한 번 돌리고, 추가 라운드는 소유자가 정함 |

## 6. 실제로 돌려서 알게 된 함정

지침·코드에 이미 반영했다. 다시 바꿀 때 같은 실수를 하지 않도록 적는다.

- 첫 지시를 `hc run ... claude "<지시>"`처럼 실행 인자로 주면 사라진다. 신뢰 창이 뜨거나 `--resume`일 때 그랬다. 그래서 엔진은 띄우고, `hc wait`로 준비를 기다린 뒤 `hc send --wait`로 짧은 메시지를 보낸다. 메시지는 지시서 파일 경로 하나다.
- 새 git worktree를 작업자 cwd로 쓰면 폴더 신뢰 창이 뜬다. 엔진은 화면에서 신뢰 창을 알아채면 키를 누르지 않고 `BLOCKED.md`를 쓴다. 그래서 작업자는 이미 신뢰된 office 체크아웃 안의 `jobs/<과제>/`에서 띄우고, 코드는 `repo/` 하위 폴더에서 고친다.
- 작업자가 이전 라운드를 기억하고 "할 일 없음"이라며 결과 파일 없이 끝낸 적이 있다. 지시서에 "결과 파일이나 `BLOCKED.md` 중 하나를 반드시 쓴다"를 넣었다.
- 검사 단계가 테스트가 만든 `__pycache__`를 미커밋 변경으로 셌다. 그래서 git 상태는 테스트 전에 본다.
- `--setting-sources project`를 주면 사용자 전역 허용 규칙이 새지 않는다. 대신 제품 저장소의 `.claude/settings.json`도 읽지 않는다.
- 리뷰할 PR이 이 저장소 자신의 PR이면 원본 `prompts/review.md`는 `git show origin/main:... | python3 -I -`를 요구한다. 파이프라서 통째로 거부되고 작업자는 `BLOCKED.md`를 썼다(PR #17). 그래서 리뷰 지시서가 office 체크아웃의 스크립트를 돌리게 한다(지금은 `engine.py preflight <과제>`). office 체크아웃이 PR 브랜치에 있으면 PR 쪽 판이 돌므로, 엔진은 office 체크아웃 전체가 `origin/main`과 같고 커밋하지 않은 변경이 없을 때만 리뷰 단계를 띄운다(지시서·단계표·검사 스크립트가 모두 이 체크아웃에서 나온다). 다르면 `BLOCKED.md`를 쓴다. `pr.txt`의 PR이 다른 저장소의 것임이 확실할 때만(PR URL과 office의 origin이 다른 owner/repo) 이 확인을 건너뛴다. 이 저장소의 PR은 `origin/main`에 있는 체크아웃에서 돌린다.
- 리뷰 작업자가 "Review 칸은 소유자가 채운다"며 PR 본문을 고치지 않은 적이 있다(PR #17). 지시서에 본문을 올리는 명령을 적었다.
- 이 저장소 자신을 제품으로 삼으면 `jobs/<과제>/repo/`가 office 체크아웃 안에 생긴다. 원본의 `tools/test_distribution.py` 링크 검사는 git이 무시하는 폴더도 훑어서, 과제가 남아 있는 동안 office 체크아웃의 test 명령이 실패한다. 깨끗한 체크아웃이나 CI에서는 통과한다.
- leader는 방향을 묻지 않고 등록하거나, `INTENT.md`를 채우지 않고 `run`을 돌리려 한 적이 있다(#13 첫 시도). leader 규칙에 순서를 적은 뒤에는 지켰다. leader가 `engine.py` 명령 뒤에 `; echo`나 `>`를 붙이면 허용한 명령도 사람 확인을 받는다.
- leader 규칙에 `--base origin/main`이 없을 때는 로컬 `main`에서 worktree가 만들어졌다. 로컬 `main`은 낡았을 수 있다.
- 제품 저장소가 mise를 쓰면 새 과제 worktree의 `.mise.toml`이 신뢰되지 않아 shim으로 잡힌 도구(cargo 등)가 실패한다(smallnext PR #389). 사람이 `mise trust <job>/repo/.mise.toml`을 한 번 한다. 신뢰된 설정을 작업자가 고치지 못하게 거부 목록에 넣었으나, 2026-11-14에 거부 목록을 줄이면서 지웠다. 지금은 막는 장치가 없다.
- #14(2026-11-11, Claude Code 2.1.322, sonnet): 같은 장난감 저장소에 과제 둘을 등록하고 `run`을 동시에 돌렸다. 설계·구현·검사 단계가 모두 끝났고, hc 터미널 이름(`<과제>-<단계>`)과 `jobs/` 파일은 섞이지 않았다. 구현 단계 도중 한쪽 엔진을 `kill -9`하자 작업자 터미널이 남았다. 엔진의 hc 터미널은 엔진의 자식이 아니라서 함께 끝나지 않는다. 다시 `run`하면 같은 이름의 `hc run`이 실패해 traceback과 종료 코드 1로 끝났다. 그래서 `run`은 과제마다 `run.lock`을 잡아 같은 과제의 엔진이 둘 돌지 못하게 하고, 작업자를 띄우기 전에 같은 이름의 남은 터미널을 닫는다. hc 터미널 이름은 hc 전체에서 하나이므로, 이름 앞에 jobs 폴더 경로의 해시 4자를 붙여 다른 체크아웃의 작업자를 닫지 않는다(`<해시>-<과제>-<단계>`). `hc run`이 실패하면 `BLOCKED.md`를 쓰고, 작업자가 시작하지 않았으므로 세션을 쓴 것으로 기록하지 않는다. 고친 뒤 남은 터미널이 있는 과제를 다시 돌려 PR 대기까지 갔다. 첫 세션(설계) 도중 엔진을 죽여 `state.json`이 없는 과제도 다시 돌리니 같은 세션 ID로 설계를 다시 하고 승인 대기까지 갔다. 180초를 넘는 단계는 `run_worker`로 작업자에게 200초 걸리는 명령을 시켜 봤다. `hc send --wait`가 180초에 timeout을 내고 `hc wait` 반복으로 넘어가, 턴이 끝난 208초에 돌아왔다. Claude Code는 단독 `sleep 200`을 막는다. 긴 테스트는 그런 명령으로 쓰지 않는다.
- 2026-11-14: 소유자 결정으로 작업자 sandbox, 검사 단계 `sandbox-exec`, `SAFE_GIT`, 단계별 허용 목록, `engine.py allow`, 읽기 제한과 판 검사, PR 본문 파일의 링크·sha 검사를 지웠다. 작업자는 `dontAsk`에서 일반 개발 도구를 쓰고, 거부 규칙은 push·`gh pr merge`·`gh pr edit`·`gh api` 쓰기만 남는다. 지운 장치의 실측과 함정은 52f77a1의 이 파일에 있다.
- 2026-11-12, 스킬 실측(Claude Code 2.1.322): 엔진의 `claude_argv`로 리뷰 단계 인자를 그대로 만들어 `hc`로 작업자를 띄웠다. 과제 폴더 `jobs/skill-probe-*`에 빈 제품 저장소를 두고, 작업자에게 스킬 목록을 파일로 옮겨 적고 이름마다 `Skill` 도구를 한 번씩 부르게 했다. 세 번 돌렸다.
  - 지금 설정 그대로: 개인 스킬(`~/.claude/skills`, Matt 포함)과 사용자 설정이 켠 플러그인(pstack)은 목록에 없고 부르면 `Unknown skill`이다. 과제 폴더의 `.claude/skills`와 office 체크아웃의 `.claude/skills`에 둔 스킬은 보이고 불렸다. Claude Code 내장 스킬(`code-review`, `simplify`, `security-review` 등)도 보인다.
  - office 체크아웃의 `.claude/settings.json`에 `enabledPlugins`로 `mattpocock-skills@mattpocock`과 `pstack@cursor-plugins`를 켜면: Matt의 자동 가능 스킬(`mattpocock-skills:tdd`, `:code-review`, `:codebase-design`, `:diagnosing-bugs` 등 11개)과 `pstack:setup-pstack`이 보이고 불렸다. `ask-matt`·`pstack:interrogate`는 목록에 없고, 부르면 `disable-model-invocation` 때문에 쓸 수 없으니 사람에게 실행을 요청하라는 오류가 났다(§3과 같다). 사용자 설정의 `extraKnownMarketplaces`는 읽지 않았지만 켜졌다. 두 플러그인이 이미 플러그인 캐시에 있었다.
  - 함정: 앞 이름 없는 `code-review`는 플러그인을 켜도 Claude Code 내장 `/code-review`로 간다(「Running in the background」로 떴다). Matt 스킬은 `mattpocock-skills:code-review`로 불러야 한다. 리뷰 지시서는 이 이름을 쓴다. 앞 이름 없는 `tdd`·`codebase-design`은 Matt 스킬로 갔다.
  - `Skill` 도구는 리뷰 단계 허용 목록에 없는데도 `--permission-mode dontAsk`에서 돌았다. 허용 목록으로는 단계마다 스킬을 고르지 못한다.
  - 제품 저장소(`repo/.claude/skills`)에 커밋한 스킬은 작업자가 `repo/` 안 파일을 Read한 뒤에도 목록에 없고 `Unknown skill`이었다. PR이 스킬로 리뷰 작업자에게 지시를 넣는 길은 이 시험에서는 열리지 않았다.
  - 확인한 것: mattpocock-skills 1.3.2에는 `hooks/`와 `.mcp.json`이 없고, 두 플러그인의 `SKILL.md`에 `allowed-tools`가 없다. pstack 0.15.36에는 `hooks/`가 없고 `agents/`가 있다. 확인하지 않은 것: 스킬이 자기 폴더의 참고 파일을 Read로 읽을 수 있는지, 스킬 본문의 절차가 단계 허용 목록 안에서 끝까지 도는지, `check_skills.py`.
  - 곁가지: 2.1.322는 단계표의 `Write(<과제>/state.json)` 같은 거부 규칙이 「never matches」라며 `Edit(...)`를 쓰라고 경고한다. 같은 경로의 `Edit(...)` 거부가 함께 있어 빈틈은 아니다. 단계표는 소유자 승인 없이 고치지 않으므로 그대로 둔다.
- 2026-11-12, 리뷰 위임 실측(Claude Code 2.1.322): 공개 장난감 저장소 `minjunkim-dev/office-toy`(시험 뒤 지울 것)의 PR #1(REVIEW.md에 「스킬 위임」 절)을 엔진 리뷰 단계로 돌렸다. 단계표 인자 그대로, 과제를 `review` 단계에 두고 `engine.py run`.
  - 작업자 폴더는 git 저장소가 아니라 `check_skills.py`를 직접 돌리면 git이 실패한다. 그래서 `engine.py skills <과제> <base>`가 `repo/`에서 `--harness claude`로 돌린다. 작업자 세션에서 플러그인 캐시를 읽어 종료 코드 0과 `code-review` 경로·sha256(1.3.2, `9c921a8d8813`)을 출력했다.
  - 판은 고정되지 않는다. office `.claude/settings.json`은 `enabledPlugins`만 둔다. 같은 이름 `mattpocock` 마켓플레이스가 사용자 범위(`~/.claude/plugins/known_marketplaces.json`)에 ref 없이 있으면 프로젝트 설정의 ref는 듣지 않는다(PR #4 리뷰). 이 실측에 실린 것은 캐시 폴더 이름이 `1.3.2`여도 upstream `main`의 `4ae2317`이다(태그 `v1.3.2`는 `d1478fc`, `code-review/`는 두 커밋 사이에 같다). 판을 고정하려면 실린 `gitCommitSha`로 확인한다. 2026-11-12에 소유자가 정했다: 판은 고정하지 않고 기록만 한다. 리뷰 보고 「확인한 것」의 `check_skills.py` 경로·sha256이 그 기록이다. 판이 다르면 작업자를 멈추는 안(PR #7)은 플러그인을 올릴 때마다 모든 과제를 멈추고 단계표 승인을 요구해서 닫았다.
  - 작업자는 `mattpocock-skills:code-review`를 불렀고, 스킬이 두 축을 하위 에이전트로 띄웠다. `Agent` 도구도 허용 규칙 없이 `dontAsk`에서 돌았다. 하위 에이전트는 지시대로 `pr.diff`를 Read로 읽었고, `cd ... && python3 -m unittest`는 거부됐다(부모의 `engine.py test` 결과로 대신했다). 보고를 `review.md`와 PR 본문에 쓰고 엔진은 `done`으로 끝났다.
  - 함정: 작업자의 `engine.py test`가 `repo/`에 `__pycache__/`를 남겨, 보고 직전 preflight가 `worktree: different`를 냈다. 작업자는 원인을 확인하고 넘어갔다. 제품 저장소의 `.gitignore`에 없으면 매번 나온다.
  - 첫 실행은 작업자 CLI 계정의 5시간 한도에 걸려 첫 응답 전에 끝났고, 엔진은 「review.md 없음」 두 번으로 `BLOCKED.md`를 썼다. 한도 오류와 작업 실패를 구별하지 못한다.
- 2026-11-12, leader 수동 스킬 요청 실측(Claude Code 2.1.322, Opus 5.5 medium): `hc`로 `leader/`에 대화형 Claude Code를 띄우고, 장난감 저장소에 막연한 요청("계산 기능을 좀 더, 뭘 넣을지는 모르겠다")을 줬다.
  - 처음 규칙("요청이 막연하면 `grill-with-docs`를 권한다")으로는 leader가 방향 세 가지를 묻고, 과제를 등록하고, 엔진을 돌린 뒤에야 "다음에는 `grill-with-docs`로" 권했다. 그래서 규칙에 "방향을 묻기 전에, 등록하기 전에 권한다"를 더했다. 고친 뒤 새 leader 세션은 방향 질문 안에 `grill-with-docs` 권유를 함께 넣었다.
  - 설계 승인 대기(종료 코드 3): 작업자 설계의 Door가 되돌리기 쉬움이라 leader가 "`interrogate`·`blast-radius`는 권하지 않는다"고 밝히고 승인을 요청했다. Door가 어려움인 설계와 병합 뒤 `retro` 권유는 돌려 보지 않았다.
- 2026-11-12, 설계·구현 스킬 실측(Claude Code 2.1.322): 로컬 장난감 과제 `toy-tdd`(calc.py에 `multiply`)를 설계 → 승인 → 구현 → 검사까지 엔진으로 돌렸다.
  - 설계 작업자는 지시대로 `design.md`에 「테스트할 seam」 목록(공개 인터페이스 하나, 경우 다섯)을 적었다. `codebase-design`은 부르지 않았다(인터페이스가 정해진 과제라 지시서상 부를 때가 아니다).
  - 구현 작업자는 `mattpocock-skills:tdd`를 불렀고, 테스트를 먼저 쓰고 `engine.py test`로 실패를 본 뒤 구현해 통과시켰다(빨강 → 초록). seam을 사용자에게 묻지 않고 승인된 목록을 따랐다. 커밋 하나, 테스트 5개 통과.
  - 함정: 작업자의 `engine.py test`가 남긴 `__pycache__/`를 검사 단계가 커밋하지 않은 변경으로 보아 두 번 실패했다. 작업자의 `rm`은 거부되어 `BLOCKED.md`를 썼다. 그래서 엔진이 돌리는 테스트에 `PYTHONDONTWRITEBYTECODE=1`을 준다. Python 말고 다른 도구의 산출물은 제품 저장소의 `.gitignore`에 있어야 한다. 고친 뒤 캐시를 지우고 검사 단계만 다시 돌려 통과(`pr.txt` 대기)를 봤다. 구현 단계부터 다시 돌리지는 않았다.
- 2026-11-12, 초안 PR 단계 실측(Claude Code 2.1.322, sonnet): `minjunkim-dev/office-toy`에 버그 과제 `toy-mean`(`mean([1, 2])`가 1을 낸다)을 등록하고 설계 → 승인 → 구현 → 검사 → 초안 PR → Ready → 리뷰를 엔진으로 끝까지 돌렸다(PR #3, 종료 코드 0).
  - 엔진이 `office/toy-mean`을 push하고 draft PR을 열고 `pr.txt`를 썼다. draft인 동안 `run`은 종료 코드 3으로 기다렸고, Ready로 바꾼 뒤 리뷰 단계가 돌았다.
  - 구현 작업자는 `mattpocock-skills:diagnosing-bugs`를 먼저, 이어서 `:tdd`를 불렀다(빨강 `1 != 1.5` → 초록). 설계 작업자는 `codebase-design`을 부르지 않았다(정할 인터페이스가 없다). 리뷰 작업자는 `engine.py skills` 종료 코드 0을 받고 `:code-review`를 두 축으로 불렀고, 보고에 플러그인 판과 `SKILL.md` sha256을 적었다.
  - `check_skills.py` 종료 코드 2: `AGENTS.md`를 바꾸는 PR #2를 리뷰 단계만 돌렸다. 작업자는 종료 코드 2를 받고 위임하지 않고 두 축을 직접 리뷰했다. 곁가지: 작업자가 `gh pr view 2`처럼 번호만 넘기자 office 저장소의 PR로 해석됐다. 작업자는 `pr.txt`의 URL로 바꿨다.
  - 측정하지 않은 것: 검사 실패 뒤 구현 재시도(검사가 한 번에 통과했다. 단위 테스트는 있다), `check_skills.py` 종료 코드 1(사용자 홈의 스킬을 지워야 한다).
  - 함정: hc 터미널은 holder 프로세스(`hc hold`)가 띄운다. holder가 Claude Code 세션 안에서 시작되면 모든 작업자가 그 세션의 환경 변수(`CLAUDE_CODE_CHILD_SESSION` 등)를 물려받는다. 그러면 작업자가 대화 기록을 저장하지 않아 hc가 턴 끝을 알아채지 못하고 엔진이 멈춘다. `hc server restart`는 holder를 남긴다. 깨끗한 셸에서 `hc server stop` 뒤 `hc server start`로 띄우고, 엔진도 깨끗한 셸에서 돌린다. 엔진을 띄운 셸의 환경도 넘어가는지는 확인하지 않았다.
  - 곁가지: 작업자의 git은 사용자 전역 git 설정을 읽는다. `core.hooksPath`가 홈을 가리켜 커밋 때 `post-commit: Operation not permitted`가 나왔다. 커밋은 됐다.

- 2026-11-12, leader부터 리뷰까지 한 번에(Claude Code 2.1.322, 작업자·leader 모두 sonnet): `minjunkim-dev/office-toy`에 `toy-rename`(`mean`을 `arithmetic_mean`으로, 옛 이름 없앰)을 leader 세션에 사람 역할로 주고 끝까지 돌렸다(PR #4, 종료 코드 0).
  - 접수: leader는 등록 전에 이름 후보 세 가지를 물었다. 요청이 막연하지 않다며 `grill-with-docs`는 권하지 않았다. 고른 뒤 `--base origin/main`으로 등록하고 `INTENT.md`의 「하지 않을 것」에 옛 이름을 남기지 않는다고 적었다.
  - 승인 대기: leader가 `blast-radius`를 실행 폴더·금지 폴더와 함께 권했다. 설계 작업자는 Door를 쉬움으로 봤다(외부 호출자 없음). 그래서 `interrogate` 권유와 어려운 PR의 봇 리뷰 요청(§4 남은 결정)은 이번에 돌지 않았다.
  - 구현 작업자는 `mattpocock-skills:tdd`로 빨강(`ImportError`) → 초록. 검사 통과, 엔진이 초안 PR을 열고 Ready를 기다렸다. 사람이 Ready로 바꾼 뒤 리뷰 작업자가 `engine.py skills` 종료 코드 0을 받고 `mattpocock-skills:code-review`를 불렀다. diff가 작아 축별 하위 에이전트는 띄우지 않고 두 축을 직접 봤다. CodeRabbit 건너뜀은 「없음(건너뜀)」으로 판정했다(repo-baseline #129 규칙대로).
- 2026-11-14, 모델 배치: 작업자는 `--setting-sources project`로 hc에서 돌아 사용자의 모델 설정을 받지 않는다. 그래서 엔진이 단계마다 `--model`을 준다(`stage_model`). leader 폴더에서 `claude -p ... --output-format json`의 `modelUsage`는 `leader/.claude/settings.json`(`"model": "opus"`)이 있으면 `claude-opus-5-5`, 없으면 `claude-haiku-5-5`였다(Claude Code 2.1.323).
- 2026-11-14, 사람 관문 둘: 설계 승인과 병합 말고는 엔진과 leader가 돈다. 엔진이 PR을 늘 Ready로 바꾸고, 쉬움이 아닌 PR이면 리뷰 봇에 한 번 요청한다. 다시 하면 풀릴 오류는 단계마다 한 번 다시 해 본다. 막힘의 답은 `INTENT.md` 결정 기록이고, 다시 띄운 작업자에게는 엔진이 첫 메시지로 다시 읽으라고 한다(지시서 문장만으로는 이어 받은 세션이 두 번 다시 읽지 않았다). 리뷰가 끝나면 `REPORT.md`를 쓴다. `new`는 `<base>`를 fetch하고, setup이 통과할 때까지 `BLOCKED.md`를 둔다(#33 리뷰의 P2 두 개).

## 7. 다시 재현하는 법

단계마다 모델은 단계표의 `model`이 정한다(설계 `sonnet`, 구현 `difficulty`, 리뷰 `door`). 토큰을 더 적게 쓰려면 단계표를 복사해 리뷰 단계의 `model`을 `sonnet`으로 바꾸고 `OFFICE_STAGES`로 지정한다. 구현 단계까지 `sonnet`으로 바꾸면 쉬운 과제의 haiku가 사라진다.

```bash
# 장난감 제품 저장소
mkdir -p /tmp/toy && cd /tmp/toy && git init -q -b main
printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n' > test_calc.py
printf '__pycache__/\n' > .gitignore
printf '# toy\n\n## 명령\n\n- setup: `N/A: 없음`\n- check: `N/A: 없음`\n- test: `python3 -m unittest`\n' > AGENTS.md
git add -A && git commit -qm init

# office 체크아웃 루트에서(이 폴더는 Claude Code에서 한 번 신뢰해 둔다. baseline.path를 먼저 적는다)
python3 pipeline/engine.py new toy-subtract --repo /tmp/toy   # test 명령은 AGENTS.md 「명령」에서 읽는다
# jobs/toy-subtract/INTENT.md에 목표를 쓴다. 예: calc.py에 subtract(a, b)와 테스트를 더한다
OFFICE_STAGES=/tmp/stages-sonnet.json python3 pipeline/engine.py run toy-subtract   # exit 3: 설계 승인 대기
python3 pipeline/engine.py approve toy-subtract                                       # 사람이 design.md를 읽은 뒤
OFFICE_STAGES=/tmp/stages-sonnet.json python3 pipeline/engine.py run toy-subtract   # exit 4: origin이 GitHub이 아니라 초안 PR을 못 연다. GitHub 저장소면 리뷰까지 돌고 exit 0
python3 pipeline/engine.py workers
```

권한 시험은 엔진의 `claude_argv()`로 단계와 같은 인자를 만든다. 그다음 `run_worker()`로 작업자를 띄우고 "소유자의 권한 시험"이라고 밝혀서 시킨다. 밝히지 않으면 모델이 규칙을 보고 아예 시도하지 않는다. 그러면 도구가 막는지 확인할 수 없다.

## 8. 이어받을 때 주의

- 정본은 원본 `minjunkim-dev/repo-baseline`이다. 리뷰 프롬프트·preflight·프로필(matt-skills, pstack) 규칙을 바꿔야 하면 원본에서 바꾼다. 이 저장소에는 원본 파일의 사본을 두지 않는다.
- 리뷰 지시서(`pipeline/prompts/review.md`)는 원본의 두 곳에 기대어 있다. matt-skills README 「리뷰」 절의 1·3·4번 번호와 `review_preflight.py --json`의 키 이름이다. 원본에서 둘 중 하나가 바뀌면 이 지시서도 맞춘다. 2026-11-12(`f6566c4`)에 둘 다 맞는 것을 확인했다.
- 원본 `tools/review_preflight.py`에 자동 보안 리뷰가 지적한 셸 인용 문제가 있었다. 엔진은 #128에서 생긴 `--json`의 리스트(`diff_argv`)를 쓰지 않고 `base`·`head`로 diff를 직접 만든다. 텍스트 출력에 그 문제가 남았는지는 확인하지 않았다. 보안 지적이므로 공개 이슈가 아니라 비공개 경로로 알린다(POLICY §7).

## 9. 이어받는 세션에 줄 프롬프트

아래를 그대로 붙여 넣는다.

```text
repo-office의 개발 하네스 작업을 이어받아 줘.

1. 먼저 읽을 것(이 순서): HANDOFF.md, AGENTS.md, 원본 repo-baseline의 POLICY.md.
   원본 로컬 클론 경로는 baseline.path에 있다.
2. HANDOFF.md §4의 남은 결정 중 아직 정하지 않은 것을 설계하거나 고치기 전에 하나씩 물어봐.
3. 내가 정하면 이슈 하나·PR 하나씩 진행해.
4. 지킬 것:
   - 저장소 규칙(AGENTS.md, POLICY.md)이 먼저다. PR 본문 Summary에 완료 기준.
   - 프로필이나 리뷰 프롬프트 규칙을 바꿔야 하면 원본 repo-baseline에서 한다.
   - 단계표(stages.json)·지시서·leader 규칙은 내 승인 없이 바꾸지 않는다.
   - 병합하지 않는다. main에 push하지 않는다.
   - 확인하지 않은 것은 확인하지 않았다고 적는다.
```
