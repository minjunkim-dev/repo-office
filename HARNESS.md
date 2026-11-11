# 개발 하네스 전체 그림과 스킬 연결

이 문서는 다른 계정·세션이 "소유자가 만들려는 개발 하네스 전체"를 이해하고 바로 이어 가기 위한 기록이다. 2026-11-11에 썼다. office 엔진의 상태와 함정은 [HANDOFF.md](HANDOFF.md)에 있고 여기에 다시 적지 않는다. 이 문서는 그 위의 층, 곧 목표·구성 요소·스킬 연결의 빈틈·다음 결정을 다룬다.

## 1. 소유자가 원하는 것

2026-11-11에 소유자가 확인했다. 처음 읽어 낸 목표를 저장소 규칙·원본 이력·설치본과 대조해 세 곳을 고친 판이다.

- 소유자가 이슈나 개발 요청 하나를 던지면, 정해진 파이프라인이 그것을 PR 직전까지 끌고 간다. 지금 규칙으로는 사람이 PR을 열고([AGENTS.md](AGENTS.md)), 엔진이 리뷰를 한 번 돌린다. 병합은 사람이 한다. PR 열기 자동화는 §8의 6이다.
- 모델이 부를 수 있는 스킬(`code-review`, `tdd`, `diagnosing-bugs`, `codebase-design` 등)은 단계마다 작업자가 부른다. 사람만 부를 수 있는 스킬(Matt 뼈대, pstack 대부분, §5)은 알맞은 때에 leader가 사람에게 실행을 요청한다. 어느 단계에서든 사람이 직접 부를 수도 있다.
- 기계로 막을 수 있는 규칙(허용 도구, 쓰기 범위, `main` push·병합 금지, 완료 증거)은 지시가 아니라 도구 권한·sandbox·CI로 지킨다. 판단이 필요한 것(Door, 병합)은 사람이 한다.
- repo-baseline 규칙이 바닥이다. office, 스킬 묶음, 개인 도구는 그 위에 얹는 선택 층이고(§2), 선택 층은 하나가 빠져도 나머지가 돌아야 한다.

## 2. 구성 요소

| 구성 요소 | 어디 | 맡는 층 | 지금 상태 |
| --- | --- | --- | --- |
| repo-baseline(원본) | `minjunkim-dev/repo-baseline`, 로컬 `~/Workspace/01_Projects/03_SideApp/repo-baseline` | PR이 `main`에 들어가는 조건: 완료 증거, 다른 컨텍스트 리뷰, Door, 사람 병합, GitHub 설정, 회고 | 코어 4파일 + 선택 프로필. 2026-11-11 `main` = `49214f2` |
| repo-baseline-office(이 저장소) | `minjunkim-dev/repo-baseline-office` | PR 전 작업을 단계로 돌린다: 설계 → 사람 승인 → 구현 → 검사 → (사람이 PR) → 리뷰 | 엔진·권한·sandbox 검증 끝. 스킬 연결 없음(§4) |
| Matt Pocock 스킬 | 개인 스킬 `~/.config/mattpocock-skills/<sha>/skills` → `~/.agents/skills`·`~/.claude/skills` symlink. 플러그인 `mattpocock-skills@*`는 사용자 설정에서 꺼져 있다 | 개발 절차의 뼈대(재설계 전), 지금은 리뷰 두 축·회고·PR 본문([matt-skills 프로필](../profiles/matt-skills/README.md)) | 설치됨. office 작업자에서 쓰는지 미확인(§5) |
| pstack | 플러그인 `pstack@cursor-plugins` 0.15.36(켜짐). 원본 `~/.local/share/pstack-plugin` | 설계 질문(`interrogate`), 영향 범위(`blast-radius`), 검증 스킬 만들기, 교정(`correct`) | 설치됨. [pstack 프로필](../profiles/pstack/README.md)은 "사람이 부를 때만" |
| 강연 원칙(Readray 노트 10개) | 원본 재설계안(`f31431f^:docs/archive/redesign-2026-11.md`) | 브레이크 3겹, `REVIEW.md`, 증거, Door, 리뷰 3라운드, 반복 지적은 환경으로 | 원본 POLICY·템플릿에 반영됨 |
| office 참고 영상 2편 | [HANDOFF.md](HANDOFF.md) §3 | 대장 하나 + 단계표 + 설계 승인 해시 + 단계별 권한 | `pipeline/`로 구현됨 |
| 개인 도구 | `hc`(herdr-core), ai-memory, trail, Semgrep CLI 등 | 작업자 띄우기, 세션 간 기억 | 엔진이 `hc`를 쓴다 |
| 소비 레포 | BGLike(Godot), smallnext 등 | 실제 과제 | office로 실제 과제를 돌린 기록이 있다(HANDOFF §4의 BGLike·smallnext 항목). 세부는 이 문서에서 확인하지 않았다 |

## 3. 스킬이 개발 절차에서 빠진 경위

소유자가 기억하는 "스킬로 굴리는 워크플로우"는 실제로 있었다. 재설계와 원본 맞추기에서 빠졌다.

| 날짜 | 어디 | 무엇 |
| --- | --- | --- |
| 2026-10-26 | 원본·이 저장소 #3 `9d3958b` | `docs/development-workflow.md` 신설. "개발 절차는 Matt Pocock 스킬이 맡는다" |
| 2026-10-27 | 이 저장소 #6 `f03a091` | 스킬 사용법을 옮겨 적지 않고 원문을 쓰게 줄임. 시작점은 `/ask-matt` |
| 2026-11-03 | 원본 CHANGELOG | pstack 배치: `interrogate`, `blast-radius`, `create-verification-skill`, `correct`만. pstack `tdd`·`reflect`·`teach`는 Matt와 겹쳐 쓰지 않음 |
| 2026-11-08 | 원본 `55acc14:docs/development-workflow.md` | "Matt 스파인": 사용자가 `/ask-matt` → `/grill-with-docs` → `/to-spec` → `/to-tickets` → `/implement`. 독립 리뷰는 `/matt-code-review`(저장소에 복사한 code-review). `disable-model-invocation` 스킬은 사용자가 실행하고 에이전트는 요청만 한다 |
| 2026-11-08 | 원본 #73·#74 `50cbc15` | 재설계: "어떤 하네스든 `cp`만으로". 스킬·pstack은 전부 선택으로 내림. `vendor/mattpocock-skills/` 삭제 |
| 2026-11-10 | 원본 #113 `b3a8985`, #114 `06751a9`, #116 | matt-skills 프로필(리뷰 두 축·회고·PR 본문 위임 + `check_skills.py`), pstack 프로필(겹침 표, 사람이 부를 때만) |
| 2026-11-10 | 이 저장소 #10 `24e227d` | 원본 `c3f6d56`에 맞추며 `docs/development-workflow.md` 삭제. office를 단계 파이프라인으로 다시 지음. office의 어느 판에도 스킬 연결은 없었다 |
| 2026-11-11 | 원본 #127 `f31431f` | `docs/archive` 삭제. 옛 기록은 git에서만 읽는다 |

옛 문서 읽기(원본 로컬 클론에서):

```bash
git show 55acc14:docs/development-workflow.md          # 재설계 직전의 Matt 스파인과 pstack 배치
git show f31431f^:docs/archive/redesign-2026-11.md     # 재설계안(강연 원칙 표, 스킬을 선택으로 내린 이유)
git show f31431f^:docs/archive/CHANGELOG-2026.md       # 재설계 전 변경 기록
```

## 4. 지금 검증된 것과 아닌 것

- 검증됨: 단계 순서, 설계 승인 해시, 검사 실패 재시도, `BLOCKED.md`, 작업자 권한(병합·`main` push·`.env` 읽기 불가), 두 sandbox, Claude Code 판 확인. 단위 테스트 31개와 장난감 저장소 실제 실행, PR #24 리뷰 실행(`gh pr edit`·push 제외). 세부는 [HANDOFF.md](HANDOFF.md) §4.
- 검증 안 됨: 스킬이 끼는 모든 경로. office 지시서(`pipeline/prompts/*.md`)와 단계표에 스킬 이름이 없다.
- 규칙은 반쯤 연결되어 있다. 지시서가 POLICY·`prompts/review.md`를 가리키고, 허용 목록이 병합·`main` push를 막는다. 스킬은 연결되어 있지 않다.

## 5. 스킬 호출의 사실

자동 호출과 수동 호출은 우리가 정하는 것이 아니라 스킬 작성자가 frontmatter로 정한다. `disable-model-invocation: true`인 스킬은 사람이 이름을 쳐야만 돈다. 모델도, 다른 스킬도 부를 수 없다(Matt 스킬의 `.agents/invocation.md`, 공식 문서 [skills](https://code.claude.com/docs/en/skills) 「Control who invokes a skill」, 2026-11-11 확인). Codex는 같은 뜻을 `agents/openai.yaml`의 `allow_implicit_invocation: false`로 읽는다(`~/.agents/tools/pstack_codex_policy.py`가 pstack에 넣는다).

2026-11-11에 설치본을 직접 확인한 구분:

| 묶음 | 모델이 부를 수 있음(자동 가능) | 사람만 부름(수동) |
| --- | --- | --- |
| Matt(개인 스킬 `4ae2317…`, 플러그인 캐시 1.3.2와 같은 구분. 이 sha의 판은 확인하지 않음) | `code-review`, `tdd`, `diagnosing-bugs`, `grilling`, `research`, `prototype`, `domain-modeling`, `codebase-design`, `pr`, `writing-for-agents`, `wizard`, `setup-pre-commit`, `git-guardrails-claude-code`, `migrate-to-shoehorn`, `scaffold-exercises` | `ask-matt`, `grill-with-docs`, `grill-me`, `to-spec`, `to-tickets`, `implement`, `implement-spec`, `triage`, `retro`, `wayfinder`, `improve-codebase-architecture`, `handoff`, `teach` 등 |
| pstack 0.15.36(스킬 58개) | `setup-pstack` 하나 | 나머지 전부(`interrogate`, `blast-radius`, `create-verification-skill`, `correct`, `architect`, `tdd`, `poteto-mode` …) |

그래서 "단계에서 자동 호출"이 문자 그대로 되는 것은 위 왼쪽 칸뿐이다. Matt 스파인과 pstack은 "알맞은 단계에서 사람에게 실행을 요청한다"가 작성자 의도에 맞는 자동화다. 재설계 전 문서도 그렇게 적었다.

## 6. 단계별 스킬 후보

재설계 전 배치(§3)와 지금 프로필을 office 단계에 맞춘 후보다. 정한 것이 아니다(§8).

| office 단계 | 후보 스킬 | 호출 | 지금 작업자에서 | 비고 |
| --- | --- | --- | --- | --- |
| 접수·방향(leader와 사람) | `ask-matt`, `grill-with-docs`, `triage`; 여러 티켓이면 `to-spec`→`to-tickets`; 어려움 설계는 pstack `interrogate` | 수동 | 해당 없음(leader 세션은 사람이 쓴다) | leader가 사람에게 실행을 요청할 수 있다. leader 규칙에 아직 없다 |
| design(작업자) | `codebase-design`, `domain-modeling`, `research`, `prototype`, `grilling` | 자동 가능 | 못 씀(§7) | `INTENT.md`가 이미 스펙이면 생략 |
| approve(사람) | pstack `blast-radius` | 수동 | 해당 없음 | 결과를 `design.md`의 Door 근거로 |
| implement(작업자) | `tdd`, `diagnosing-bugs` | 자동 가능 | 못 씀(§7) | Matt `implement`는 같은 세션에서 `code-review`를 불러 POLICY §3(다른 컨텍스트 리뷰)과 부딪힌다. 파이프라인 안에서는 쓰지 않는 쪽이 맞다. pstack `tdd`는 Matt `tdd`와 겹쳐 쓰지 않는다 |
| check(스크립트) | pstack `create-verification-skill`(프로젝트당 한 번) | 수동 | 해당 없음 | 만든 검증을 `AGENTS.md`의 check·test 명령으로 남기면 검사 단계가 그대로 돌린다 |
| pr(사람) | Matt `pr` | 자동 가능 | 해당 없음 | PR 양식이 먼저다(코어 `AGENTS.md`) |
| review(다른 세션 작업자) | Matt `code-review`(matt-skills 「스킬 위임」 + `check_skills.py`) | 자동 가능 | 못 씀(§7) | 제품 `REVIEW.md`에 「스킬 위임」 절이 있어야 한다 |
| 병합 뒤 회고(사람) | Matt `retro`, pstack `correct`(제안만) | 수동 | 해당 없음 | `prompts/retro.md`가 먼저다 |

## 7. 작업자에서 스킬을 막는 것

| 막는 것 | 근거 | 확인 |
| --- | --- | --- |
| 지시서(`pipeline/prompts/*.md`)가 스킬을 부르라고 하지 않는다. 리뷰 지시서는 원본 `prompts/review.md`를 따르게 하므로 「스킬 위임」 한 줄로만 간접 연결된다 | 파일 내용 | 확인함 |
| 리뷰 단계 `tools`에 `Skill`도 `check_skills.py` 실행 규칙도 없다. 작업자는 `--permission-mode dontAsk`라 목록 밖은 거부된다 | [pipeline/stages.json](pipeline/stages.json) | 설정 확인함. `Skill` 도구가 `dontAsk`에서 허용 규칙을 요구하는지는 실행해 보지 않았다 |
| `--setting-sources project`라 사용자 설정의 `enabledPlugins`(pstack)와 개인 스킬(`~/.claude/skills`, Matt)이 작업자에 안 실릴 가능성이 크다 | 단계표 `runner`. 공식 [CLI reference](https://code.claude.com/docs/en/cli-reference)는 이 플래그가 설정 출처(user, project, local)를 고른다고만 적고, 스킬이 실리는지는 적지 않는다(2026-11-11 확인) | 실행해 보지 않았다 |
| 작업자의 읽기는 과제 폴더와 이 체크아웃으로 막혀 `~/.claude/skills`의 `SKILL.md`를 Read로 읽지 못한다. `check_skills.py`도 홈의 스킬 폴더를 읽어야 하는데 sandbox가 홈 읽기를 막는다 | [AGENTS.md](AGENTS.md) 규칙 | 설정에서 추론. 실행해 보지 않았다 |
| 사람만 부르는 스킬은 모델이 부를 수 없다 | §5 | 작성자 규칙 |

## 8. 소유자가 정할 것

1. 목표(§1): 2026-11-11에 정했다.
2. 연결을 어디에 둘까. office 안(단계표·지시서·leader 규칙)인가, 원본의 새 프로필인가. 원본 코어에 다시 넣으면 #73 재설계("어떤 하네스든 `cp`만으로")를 되돌리는 것이다.
3. 사람만 부르는 스킬을 어떻게 돌릴까. 2026-11-11에 (가)로 정했다(§1).
   - (가) leader가 알맞은 때에 사람에게 실행을 요청한다. 작성자 의도에 맞는다.
   - (나) 엔진이 `hc send`로 작업자 터미널에 `/스킬이름`을 입력한다. 기술적으로 사람 입력이라 돌지만, 작성자의 수동 표시를 우회한다.
   - (다) 작업자가 `SKILL.md`를 참고 문서로 읽는다. 역시 우회다.
4. 작업자에 스킬을 어떻게 실을까.
   - office 체크아웃의 프로젝트 설정(`.claude/settings.json`의 `extraKnownMarketplaces`·`enabledPlugins`, 원본 [templates/github/agents/README.md](../templates/github/agents/README.md)의 예)으로 플러그인을 켠다.
   - 쓰는 스킬만 office 체크아웃 `.claude/skills/`에 둔다. 하네스 폴더라 신뢰 경계 경로이고 Door는 어려움이다.
   - `--setting-sources`를 넓힌다. 사용자 전역 허용 규칙이 새므로 권하지 않는다(HANDOFF §4).
5. 원본 프로필(matt-skills·pstack)의 규칙을 바꿔야 하면 원본에서 바꾼다. 이 저장소의 `claude-office/` 밖은 원본과 같게 둔다.
6. PR 열기를 자동으로 할까. 지금은 사람이 연다. 제안: 검사 단계가 통과하면 엔진이 PR 브랜치를 push하고 초안(draft) PR을 연다. 사람은 초안을 보고 "Ready for review"로 바꾸거나 닫는다. 근거: 원본 재설계안의 원칙이 "PR은 열되 병합은 막는다"이고, 초안 PR은 닫으면 되돌아간다. 바꿀 곳은 단계표·leader 규칙·`AGENTS.md`이므로 소유자 승인이 필요하다. 원격에 처음 무언가를 올리는 단계라 Door는 어려움으로 본다.
7. office를 어디에 둘까. 지금은 원본 위에 `claude-office/`를 얹은 후보 저장소다(README). 원본을 정본으로 두는 것은 유지한다. office를 정본으로 삼으면 모든 소비 레포가 Claude Code·macOS·`hc`에 묶이고, #73의 "어떤 하네스든 `cp`만으로"가 깨진다.
   - 지금 구조의 비용: `claude-office/` 밖을 원본과 손으로 맞춘다. 이 저장소가 원본의 사본인 이유는 엔진이 상위 폴더를 repo-baseline 클론으로 쓰기 때문이다(`engine.py`의 `BASELINE = OFFICE.parent`). 리뷰 단계도 원본 판이 아니라 이 사본의 `prompts/review.md`·`tools/review_preflight.py`를 쓴다.
   - 원본의 선택 프로필(`profiles/office`)로 옮기는 안은 버린다. `profiles/`는 소비 레포가 복사해 가는 파일을 두는 곳인데, office는 소비 레포에 복사하지 않고 자기 체크아웃에서 돈다. 또 macOS·Claude Code에 묶인 코드와 테스트가 중립 정본에 들어온다.
   - 제안: office를 `claude-office/`만 담은 별도 저장소로 만들고, repo-baseline 클론의 경로를 설정으로 받는다. 그러면 맞추는 비용이 없어지고 리뷰는 원본 판을 쓴다. 지금 저장소는 보관한다. 정본을 건드리지 않으므로 office의 검증을 기다릴 필요는 없다.
   - 고칠 곳은 한 줄이 아니다. `BASELINE`은 `engine.py`에서 12번 쓰이고, 그중 작업자 sandbox의 쓰기 금지 경로(지금은 `BASELINE` 아래에서 `OFFICE`를 뺀 나머지를 막는다), 검사 단계 sandbox의 읽기 경로, repo-baseline 자신의 PR을 리뷰할 때 클론이 `origin/main`과 같은지 보는 검사가 이 배치를 전제한다. 신뢰 경계를 바꾸므로 Door는 어려움이고, 새 저장소를 만드는 일이라 소유자가 정한다.
   - 클론 경로는 2026-11-11에 `claude-office/baseline.path`(git이 무시하는 한 줄 파일)로 정했다. 환경 변수는 `hc`로 띄운 작업자에게 전해지는지 확인하지 않아 쓰지 않았다. 없으면 지금처럼 상위 폴더를 쓴다. 리뷰 고정 검사는 클론 밖의 office 체크아웃도 `origin/main`과 비교한다.
   - 정할 것: git 기록을 옮길지(`git filter-repo` 등), office를 계속 쓸지 판단하는 기준(예: 실제 과제 N개가 사람 개입 없이 PR 직전까지 감).

## 9. 제안하는 다음 순서

각 단계는 이슈 하나·PR 하나다. 앞 단계의 실행 결과를 보고 다음을 정한다.

1. 실측: 단계표 인자 그대로 작업자를 띄워 스킬 목록에 무엇이 보이는지, `Skill` 도구가 허용 규칙 없이 도는지 본다. 완료 기준: 개인 스킬·플러그인 스킬·프로젝트 스킬 각각의 보임/호출 여부를 HANDOFF §4에 적는다. 코드 변경 없음.
2. 리뷰 위임 연결: 리뷰 단계에서 `check_skills.py` → `code-review` 위임이 실제로 돈다. 완료 기준: 「스킬 위임」 절이 있는 장난감 저장소의 실제 PR에서 리뷰 작업자가 `check_skills.py` 종료 코드 0을 받고 `code-review`를 부른다.
3. 설계·구현 단계의 자동 가능 스킬 연결(`tdd`, `diagnosing-bugs`, `codebase-design` 등). 완료 기준: 장난감 과제에서 구현 작업자가 `tdd`를 부른 기록이 있다.
4. leader의 수동 스킬 요청(§8의 3, 2026-11-11에 (가)로 정함): 접수 때 `ask-matt`·`grill-with-docs`, 어려움 설계 때 `interrogate`, 승인 전 `blast-radius`, 병합 뒤 `retro`를 사람에게 요청한다. 완료 기준: leader 규칙에 단계별 요청이 있고, 실제 과제 하나에서 그 요청이 나왔다.

## 10. 이어받는 세션에 줄 프롬프트

아래를 그대로 붙여 넣는다. 이 문서가 `main`에 병합되기 전이면 원격 브랜치에서 읽는다.

```text
repo-baseline-office의 개발 하네스 작업을 이어받아 줘.

1. 먼저 읽을 것(이 순서):
   - claude-office/HARNESS.md  (목표, 구성 요소, 스킬 연결의 빈틈, 다음 순서)
   - claude-office/HANDOFF.md  (엔진 상태와 함정)
   - claude-office/AGENTS.md, 루트 AGENTS.md, POLICY.md
   위치: main에 없으면 git fetch origin 뒤 원격 브랜치 claude/dev-harness-operation-67729c의 head. 원본 repo-baseline 로컬 클론은 ~/Workspace/01_Projects/03_SideApp/repo-baseline.
2. HARNESS.md §1의 목표는 2026-11-11에 정했다. 설계하거나 고치기 전에
   §8의 결정 중 아직 정하지 않은 것을 하나씩 물어봐.
3. 내가 정하면 §9 순서대로 이슈 하나·PR 하나씩 진행해. 첫 단계는 코드 변경 없는 실측이다.
4. 지킬 것:
   - 저장소 규칙(루트 AGENTS.md, POLICY.md)이 먼저다. PR마다 CHANGELOG.md 1~3줄, PR 본문 Summary에 완료 기준.
   - claude-office/ 밖은 원본과 같게 둔다. 프로필(profiles/) 규칙을 바꿔야 하면 원본 repo-baseline에서 한다.
   - 단계표(stages.json)·지시서·leader 규칙은 내 승인 없이 바꾸지 않는다.
   - 병합하지 않는다. main에 push하지 않는다.
   - gh 명령에는 --repo minjunkim-dev/repo-baseline-office를 붙인다.
   - 확인하지 않은 것은 확인하지 않았다고 적는다.
```
