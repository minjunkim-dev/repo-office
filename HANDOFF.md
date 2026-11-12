# 이어받기

다른 사람·계정·에이전트가 이 저장소 작업을 바로 이어받기 위한 기록이다. 목표, 구성 요소, 스킬 연결, 정한 것, 엔진의 함정을 적는다. 규칙은 [AGENTS.md](AGENTS.md)와 repo-baseline `POLICY.md`가 정본이고 여기에 다시 적지 않는다. 2026-11-12에 옛 `HARNESS.md`를 이 문서에 합쳤다. 옛 경위(재설계 전 Matt 스파인, `claude-office/` 시절의 원본 맞추기)는 git 기록에 있다.

## 1. 목표와 지금 상태

소유자가 2026-11-11에 정한 목표:

- 소유자가 이슈나 개발 요청 하나를 던지면, 정해진 파이프라인이 그것을 리뷰를 마친 PR까지 끌고 간다. 병합은 사람이 한다.
- 모델이 부를 수 있는 스킬(`tdd`, `diagnosing-bugs`, `codebase-design`, `code-review`)은 단계마다 작업자가 부른다. 사람만 부를 수 있는 스킬(Matt 뼈대, pstack 대부분, §3)은 알맞은 때에 leader가 사람에게 실행을 권한다.
- 기계로 막을 수 있는 규칙(허용 도구, 쓰기 범위, `main` push·병합 금지, 완료 증거)은 지시가 아니라 도구 권한·sandbox·CI로 지킨다. 판단이 필요한 것(설계 승인, Ready, 병합)은 사람이 한다.
- repo-baseline 규칙이 바닥이다. office, 스킬 묶음, 개인 도구는 그 위의 선택 층이고, 하나가 빠져도 나머지가 돈다.

지금 상태(2026-11-12):

- 흐름: 접수(leader) → 설계 → 사람 승인(`design.md` sha256) → 구현 → 검사(`sandbox-exec`) → 엔진이 push하고 초안 PR → 사람이 Ready → 리뷰(다른 세션) → 엔진이 리뷰 본문을 PR에 올림 → 사람이 병합.
- 이 흐름 전체를 장난감 저장소에서 엔진과 실제 작업자로 끝까지 돌렸다(leader 접수는 따로 돌렸다)(§6의 2026-11-12 항목). 단위 테스트는 44개다.
- 열린 이슈는 없다. 남은 결정은 §4에 있다.

## 2. 구성 요소

| 구성 요소 | 어디 | 맡는 층 | 지금 상태 |
| --- | --- | --- | --- |
| repo-baseline(원본) | `minjunkim-dev/repo-baseline`, 로컬 클론 경로는 `baseline.path` | PR이 `main`에 들어가는 조건: 완료 증거, 다른 컨텍스트 리뷰, Door, 사람 병합, GitHub 설정, 회고 | 코어 4파일 + 선택 프로필. 2026-11-12 `main` = `f6566c4` |
| repo-office(이 저장소) | `minjunkim-dev/repo-office`(공개). 2026-11-11에 repo-baseline-office의 `claude-office/`에서 git 기록째 옮겼다 | PR 전 작업을 단계로 돌리고, 초안 PR을 열고, 리뷰를 한 번 돌린다 | 엔진·권한·sandbox·스킬 연결·초안 PR까지 실측함 |
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
- 2026-11-12: 검사가 통과하면 엔진이 초안 PR을 연다. 사람이 Ready로 바꾸기 전에는 리뷰가 돌지 않는다.

남은 결정:

- office를 계속 쓸지 판단하는 기준(예: 실제 과제 N개가 사람 개입 없이 PR까지 감).
- repo-baseline #130의 두 규칙에 office의 주인이 없다. office에서는 엔진이 PR을 연다.
  - 되돌리기 어려움이거나 신뢰 경계를 건드린 PR에서 리뷰 봇이 건너뛰면, PR을 연 쪽이 봇에 리뷰를 한 번 요청한다. 리뷰어가 Door를 어려움으로 올렸으면 리뷰어가 요청한다. 엔진도 리뷰 지시서도 지금 요청하지 않는다. 사람이 Ready로 바꿀 때 직접 요청해야 한다.
  - 같은 PR에 다른 모델 패밀리 리뷰(Codex, Cursor agent)를 권장한다. 엔진의 리뷰 단계는 Claude 하나다. 리뷰 작업자는 원본 `prompts/review.md`대로 받지 못한 사실을 「남은 지적」에 적는다.
  - 둘 다 엔진·단계표·leader 규칙을 바꾸는 일이라 소유자 승인과 사람 병합이 필요하다.

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
- 그 cwd는 office 체크아웃 안이다. 그래서 git 쓰기 명령은 `git -C <job>/repo ...` 절대 경로 형태만 허용한다. 일반 `git commit`을 허용하면 office 브랜치에 커밋된다.
- 작업자가 `&&`·`|`로 명령을 이어 붙이면, 한 부분이라도 허용 밖일 때 통째로 거부된다. Haiku는 "하나씩"이라는 지시를 자주 어겼다. Sonnet은 지켰다.
- 작업자가 이전 라운드를 기억하고 "할 일 없음"이라며 결과 파일 없이 끝낸 적이 있다. 지시서에 "결과 파일이나 `BLOCKED.md` 중 하나를 반드시 쓴다"를 넣었다.
- 검사 단계가 테스트가 만든 `__pycache__`를 미커밋 변경으로 셌다. 그래서 git 상태는 테스트 전에 본다.
- `--setting-sources project`를 주면 사용자 전역 허용 규칙이 새지 않는다. 대신 제품 저장소의 `.claude/settings.json`도 읽지 않는다.
- 경로 제한 없는 Write·Edit는 위험하다. 작업자가 `.git/hooks`를 심으면 허용된 `git commit`이 그것을 실행한다. 지금은 과제 폴더의 md와 `repo/**`로만 좁혔다.
- 리뷰할 PR이 이 저장소 자신의 PR이면 원본 `prompts/review.md`는 `git show origin/main:... | python3 -I -`를 요구한다. 파이프라서 통째로 거부되고 작업자는 `BLOCKED.md`를 썼다(PR #17). 그래서 리뷰 지시서가 office 체크아웃의 스크립트를 돌리게 한다(지금은 `engine.py preflight <과제>`). office 체크아웃이 PR 브랜치에 있으면 PR 쪽 판이 돌므로, 엔진은 office 체크아웃 전체가 `origin/main`과 같고 커밋하지 않은 변경이 없을 때만 리뷰 단계를 띄운다(지시서·단계표·검사 스크립트가 모두 이 체크아웃에서 나온다). 다르면 `BLOCKED.md`를 쓴다. `pr.txt`의 PR이 다른 저장소의 것임이 확실할 때만(PR URL과 office의 origin이 다른 owner/repo) 이 확인을 건너뛴다. 이 저장소의 PR은 `origin/main`에 있는 체크아웃에서 돌린다.
- 리뷰 작업자가 "Review 칸은 소유자가 채운다"며 PR 본문을 고치지 않은 적이 있다(PR #17). 지시서에 본문을 올리는 명령을 적었다.
- 구현 작업자는 `git rev-parse`와 `git -C <절대 경로> status`를 썼다가 거부당했다. 두 읽기 명령을 허용에 더했다.
- 이 저장소 자신을 제품으로 삼으면 `jobs/<과제>/repo/`가 office 체크아웃 안에 생긴다. 원본의 `tools/test_distribution.py` 링크 검사는 git이 무시하는 폴더도 훑어서, 과제가 남아 있는 동안 office 체크아웃의 test 명령이 실패한다. 깨끗한 체크아웃이나 CI에서는 통과한다.
- leader는 방향을 묻지 않고 등록하거나, `INTENT.md`를 채우지 않고 `run`을 돌리려 한 적이 있다(#13 첫 시도). leader 규칙에 순서를 적은 뒤에는 지켰다. leader가 `engine.py` 명령 뒤에 `; echo`나 `>`를 붙이면 허용한 명령도 사람 확인을 받는다.
- leader 규칙에 `--base origin/main`이 없을 때는 로컬 `main`에서 worktree가 만들어졌다. 로컬 `main`은 낡았을 수 있다.
- 제품 저장소가 mise를 쓰면 새 과제 worktree의 `.mise.toml`이 신뢰되지 않아 shim으로 잡힌 도구(cargo 등)가 실패한다(smallnext PR #389). 사람이 `mise trust <job>/repo/.mise.toml`을 한 번 한다. 신뢰된 설정을 작업자가 고치지 못하게 거부 목록에 넣었다.
- 작업자는 leader가 등록한 `--allow` 규칙과 글자까지 같은 명령만 돌릴 수 있다. `Bash(make a b)`만 허용하면 `make a`는 거부된다. `--allow` 명령은 과제 폴더에서 돈다(아래 `cd` 항목). check·test는 `engine.py test <과제>`로 돌리므로 `--allow`로 등록하지 않는다. 리뷰어가 검사를 다시 돌리지 못하면 「남은 지적」에 그렇게 적는다.
- 허용 목록의 bare `Read`는 어디든 읽게 했고, `cat` 같은 읽기 전용 명령은 허용 규칙 없이도 돈다. `git fetch*`는 `--upload-pa="<명령>; git-upload-pack"`으로 명령을 실행했다(git 2.54.0에서 확인). 그래서 읽기는 작업 폴더로 막고 fetch는 `origin` 한 형태만 둔다. 2026-11-10에 실제 작업자(Claude Code 2.1.322)로 돌려, 밖의 파일 읽기 세 가지와 fetch 두 가지, `--out''put=`이 거부되고 `git -C repo fetch origin`과 이 체크아웃 읽기가 되는 것을 봤다. 그 뒤 `git -C repo diff /dev/null <밖의 파일>`이 `--no-index` 없이도 내용을 출력하는 것을 보고(git 2.54.0) diff 거부 규칙을 더했다. 같은 날 실제 작업자 둘로 시험했다. 리뷰 단계 규칙 그대로인 작업자는 밖의 파일을 읽는 diff 6가지(절대 경로, `..` 경로, 저장소 안 파일과 비교, `git diff` 형태, 중괄호 확장, 백슬래시)를 모두 거부했고 정상 diff 2가지(`HEAD~1 HEAD`, `HEAD~1..HEAD -- f`)는 돌렸다. diff 거부 규칙만 뺀 작업자도 밖의 파일을 읽는 diff 2가지와 `cat`을 거부했다. 곧 `blockReadsOutsideWorkingDirectories`는 허용 규칙에 맞는 `git diff`의 밖 읽기를 막는다. 모든 셸 명령을 막지는 않는다. 리뷰(PR #20)가 `git -C <job>/repo commit --allow-empty -F <밖의 파일>`이 돌아 파일 내용이 커밋 메시지가 되는 것과, `add --pathspec-from-file=<밖의 파일>`이 오류 문장에 내용을 출력하는 것, 허용 규칙 `git diff*`가 `git difftool -x <명령>`에도 맞는 것을 찾았다. 그래서 commit 허용을 `commit -m *`로 좁히고(git은 `-m`과 `-F`를 함께 받지 않는다) `--pathspec-f`와 `difftool`을 거부한다. 실제 작업자로 9가지를 돌려 `commit -F`, `add --pathspec-from-file=`, `commit -m x --pathspec-fr=`, `difftool -x`, 탭으로 띄운 밖 경로 diff, glob(`.?/`) 밖 경로 diff, `commit -am`이 거부되고 `commit -m probe --allow-empty`가 되는 것을 봤다. `commit -m x -F <밖>`은 돌았으나 git이 거부했다. 작업자는 이제 `commit -am`을 쓰지 못한다. diff 거부 규칙은 이 설정이 듣지 않는 판(2.1.257 미만)을 위한 보조 수단이고, 경로 문법을 다 막지 못한다(중괄호 확장, 탭, glob). 따옴표나 `$`가 든 경로는 diff에 줄 수 없다. 엔진은 작업자를 띄우기 전에 `claude --version`이 2.1.257 이상인지 확인하고, 아니면 `BLOCKED.md`를 쓴다.
- `blockReadsOutsideWorkingDirectories`가 켜지면 작업자의 Bash `cd`가 허용 규칙(`Bash(cd repo)`)이 있어도 거부된다. 2026-11-11에 실제 작업자(Claude Code 2.1.322, sonnet)로 구현 단계 인자 그대로 다섯 가지를 시험했다. 설정이 있으면 `cd repo`와 일반 하위 폴더로 가는 `cd`가 거부됐고, 제품 저장소를 `--add-dir`로 더해도 거부됐다. 설정만 빼면 `cd repo`가 되고 테스트 2개가 통과했다. 판 확인과 절대 경로(PR #21)를 뺀 경우도 똑같이 거부됐다. 그래서 PR #20 뒤로 작업자는 `repo/`에서 테스트를 돌리지 못한다. 같은 날 엔진으로 장난감 과제를 돌리니 설계 단계는 끝났고, 구현 단계는 파일만 고친 뒤 `BLOCKED.md`를 썼다(#23). 공식 문서의 `Cd` 규칙은 사용자의 `/cd`에만 적용되고([permissions#cd](https://code.claude.com/docs/en/permissions#cd)), 이 거부의 이유는 문서에서 찾지 못했다(2026-11-11 확인). 그래서 작업자는 `cd`하지 않는다. git 읽기는 `git -C repo ...`로, 테스트는 `python3 <office>/pipeline/engine.py test <과제>`로 돌린다. 엔진이 `task.json`의 `test`를 `repo/`에서 돌린다. 이 명령만 와일드카드 없이 허용한다. leader가 `--allow`로 더한 명령은 과제 폴더에서 돌므로 `make -C repo lint`처럼 `repo/`를 직접 가리키게 등록한다. 고친 뒤 같은 날 같은 판·모델로 장난감 과제를 엔진으로 돌렸다. 설계 단계가 끝났고, 구현 단계의 작업자가 `engine.py test`로 테스트 2개를 통과시키고 커밋했고, 검사 단계도 통과했다(exit 3, `pr.txt` 대기). 리뷰 단계는 실제 작업자로 돌리지 않았다. 리뷰 전 확인(`review_preflight.py`)도 현재 폴더의 저장소를 읽으므로, `cd` 없이는 office 체크아웃을 봤다(PR #24 리뷰). 그래서 엔진의 `engine.py preflight <과제>`가 `pr.txt`의 URL로 `repo/`에서 돌린다. `-C repo` 없는 git 허용 규칙도 office 체크아웃을 가리키므로 뺐다. 리뷰 단계를 실제 작업자로 돌리니 preflight가 출력한 `diff:` 명령이 거부됐다. `git -C <절대 경로>` 형태는 허용 규칙에 없고, `.env*`를 빼는 따옴표 pathspec은 PR #20의 따옴표 거부 규칙에 걸린다. 그래서 `engine.py preflight`가 종료 코드 0일 때 그 `diff:` 줄의 base·head로 같은 diff를 만들어 `<과제>/pr.diff`에 바이트 그대로 쓰고, 작업자는 그 파일을 Read로 읽는다. 엔진이 리뷰를 한 번만 돌리므로 2라운드 diff(`<직전>..<head>`)는 다루지 않는다. 그때 엔진은 출력 마지막 줄이 정해진 형식과 정확히 같을 때만 base와 head를 꺼내 diff 명령을 직접 만들었다. 앞 줄에는 PR의 파일 이름이 찍히므로, 파일 이름으로 가짜 `diff:` 줄을 넣을 수 있다(자동 보안 리뷰 지적). 2026-11-11에 원본 스크립트에 `--json`을 더해(minjunkim-dev/repo-baseline#126) 엔진은 이제 그 JSON의 `base`·`head`를 검사해 쓴다. 작업자도 그 JSON을 본다. 같은 날 리뷰 단계 인자와 지시서 그대로 실제 작업자(sonnet)에게 PR #24를 리뷰시켰다. `gh pr edit`과 push는 빼고 시켰다. `engine.py preflight`, `gh pr view`, `engine.py test`가 허용됐고, 작업자는 `pr.diff`를 읽어 리뷰를 끝냈다. 거부된 명령은 없었다. 마지막 줄 검사는 이 시험 뒤에 넣었다. 3라운드 리뷰가 UTF-8이 아닌 diff에서 엔진이 traceback으로 죽어 종료 코드 1(비밀값 파일과 같은 값)을 내는 것을 찾았다. 그래서 diff는 바이트로 받는다.
- #14(2026-11-11, Claude Code 2.1.322, sonnet): 같은 장난감 저장소에 과제 둘을 등록하고 `run`을 동시에 돌렸다. 설계·구현·검사 단계가 모두 끝났고, hc 터미널 이름(`<과제>-<단계>`)과 `jobs/` 파일은 섞이지 않았다. 구현 단계 도중 한쪽 엔진을 `kill -9`하자 작업자 터미널이 남았다. 엔진의 hc 터미널은 엔진의 자식이 아니라서 함께 끝나지 않는다. 다시 `run`하면 같은 이름의 `hc run`이 실패해 traceback과 종료 코드 1로 끝났다. 그래서 `run`은 과제마다 `run.lock`을 잡아 같은 과제의 엔진이 둘 돌지 못하게 하고, 작업자를 띄우기 전에 같은 이름의 남은 터미널을 닫는다. hc 터미널 이름은 hc 전체에서 하나이므로, 이름 앞에 jobs 폴더 경로의 해시 4자를 붙여 다른 체크아웃의 작업자를 닫지 않는다(`<해시>-<과제>-<단계>`). `hc run`이 실패하면 `BLOCKED.md`를 쓰고, 작업자가 시작하지 않았으므로 세션을 쓴 것으로 기록하지 않는다. 고친 뒤 남은 터미널이 있는 과제를 다시 돌려 PR 대기까지 갔다. 첫 세션(설계) 도중 엔진을 죽여 `state.json`이 없는 과제도 다시 돌리니 같은 세션 ID로 설계를 다시 하고 승인 대기까지 갔다. 180초를 넘는 단계는 `run_worker`로 작업자에게 200초 걸리는 명령을 시켜 봤다. `hc send --wait`가 180초에 timeout을 내고 `hc wait` 반복으로 넘어가, 턴이 끝난 208초에 돌아왔다. Claude Code는 단독 `sleep 200`을 막는다. 긴 테스트는 그런 명령으로 쓰지 않는다.
- 거부 목록은 샌드박스가 아니다. 허용한 테스트 명령은 임의 코드를 실행한다. git 와일드카드 규칙은 부분 문자열 거부로 막고 있어 근본 대책이 아니다. 그래서 #15에서 작업자의 Bash를 Claude Code sandbox에 넣었다. git 거부 규칙은 그대로 둔다. sandbox가 읽기와 쓰기를 막아도, 우회한 git 옵션이 과제 폴더 안에서 무엇을 하는지까지 막지는 않기 때문이다. 2026-11-11에 실제 작업자(Claude Code 2.1.322, sonnet)로 시험한 것:
  - 작업자가 쓴 테스트를 `engine.py test`로 돌리니 과제 폴더 쓰기는 됐고, office 체크아웃(`pipeline/`)·홈 폴더 쓰기와 `~/.zshrc` 읽기는 `Operation not permitted`였다. 허용 목록 밖의 `ls ~`는 여전히 거부됐다. 제품 저장소의 git 데이터를 `allowWrite`에 넣어야 `commit`이 된다(worktree의 커밋은 제품 저장소의 `.git`에 쓴다). 사용자 전역 git hook(`core.hooksPath`)은 sandbox가 막아 경고만 나고 커밋은 된다.
  - `denyWrite`에 office 체크아웃 전체를 넣으면 그 안의 과제 폴더를 `allowWrite`로 열어도 쓰기가 막혔다. 그래서 체크아웃의 항목 중 `jobs`만 빼고 하나씩 막는다.
  - 네트워크: `allowedDomains: ["github.com"]`으로 `git -C repo fetch origin`이 됐다. `gh`는 sandbox 안에서 `~/.config/gh`를 읽지 못하고, 읽게 해도 macOS 인증서 확인(TLS)에서 실패하고, 그것을 풀어도(`enableWeakerNetworkIsolation`) 키체인의 토큰을 읽지 못해 401이었다. 키체인을 sandbox에 열면 작업자 코드도 키체인을 읽게 되므로 열지 않는다. 대신 `gh`·`preflight`·`push`를 `excludedCommands`로 뺐고, 그러자 `gh pr view`가 됐다.
  - 고친 엔진으로 장난감 과제를 설계·구현·검사까지 돌려 PR 대기(exit 3)까지 갔다. 리뷰 단계는 실제 PR로 돌리지 않았다. 리뷰 단계의 명령은 위 `gh`·`fetch` 시험으로만 확인했다.
  - PR #28 리뷰가 찾은 것: 과제 폴더 전체를 열면 sandbox 안의 테스트가 `task.json`·`state.json`·`pr.txt`를 고친다(Edit 거부 규칙은 도구에만 듣는다). 제품 저장소의 git 데이터를 열면 `repo/.git` 파일, `config`, `hooks`도 고친다. 그 설정은 sandbox 밖에서 도는 preflight·push의 git이 따른다(`core.fsmonitor`, `pre-push`). 그래서 이 경로들과 아직 없는 지시 파일(`CLAUDE.md` 등)을 하나씩 `denyWrite`에 넣었다. 같은 날 실제 작업자로 13곳에 쓰기를 시도해, 과제 폴더의 일반 파일만 되고 제어 파일·git 설정·hook·worktree 연결 파일·새 `CLAUDE.md`·`pr.txt` 생성은 모두 `Operation not permitted`인 것을 봤다. 그 뒤에도 `git commit`은 됐다. 리뷰 2라운드는 과제 코드가 `repo/` 안에 하위 저장소(gitlink)를 커밋하고 그 gitdir 설정에 `core.fsmonitor`를 넣으면 sandbox 밖의 `git status`가 그 명령을 실행하는 것을 찾았다(git 2.54.0, sandbox 없이 재현). 파일을 하나씩 막는 대신, 엔진이 돌리는 git에 환경 변수(`GIT_CONFIG_COUNT`)로 `core.fsmonitor=false` 등을 강제했다. 하위 저장소의 git에도 넘어간다. 단위 테스트가 이 공격을 재현하고 강제가 막는 것을 확인한다.
  - 남은 구멍은 `AGENTS.md` 규칙에 적었다(단계 도중 새로 생긴 과제·worktree 폴더, `github.com`으로 내보내기, 리뷰 `push`에는 git 설정 강제가 없음). Linux는 bubblewrap과 socat이 있어야 하고 시험하지 않았다. Windows는 sandbox가 없으므로 작업자가 시작하지 않는다.
- #29(2026-11-11): 검사 단계는 엔진이 sandbox 없이 테스트를 돌려, 작업자가 쓴 코드가 사용자 권한으로 한 번 실행됐다. 그래서 엔진이 macOS `sandbox-exec`로 감싼다. Claude Code가 쓰는 sandbox runtime(`srt`)은 설치되어 있지 않아 의존성을 늘리지 않으려고 쓰지 않았다. 작업자의 `engine.py test`는 감싸지 않는다. Claude Code sandbox 안에서는 sandbox를 다시 걸 수 없다. 단위 테스트가 실제 `sandbox-exec`로 과제 폴더 쓰기와 `$TMPDIR` 쓰기는 되고, 밖의 파일·`task.json`·`pr.txt` 생성·`repo/.git`·새 `CLAUDE.md`·홈 폴더 읽기는 막히는 것을 확인한다. 같은 날 엔진으로 장난감 과제를 돌려 검사 단계가 sandbox 안에서 테스트 2개를 통과(exit 0)했고, 그 과제 폴더에서 `https://github.com` 연결, 홈 폴더 쓰기, `~/.zshrc` 읽기가 모두 `Operation not permitted`인 것을 봤다. 엔진의 `git rev-parse`·`git status`는 sandbox 밖이지만 `SAFE_GIT`을 쓴다. BGLike(Godot)의 `make godot-check`가 sandbox 안에서 홈 쓰기(`~/Library/Application Support/Godot`)와 `~/.gitconfig` 읽기로 실패해서, 검사 단계는 `HOME=<과제>/tmp/home`과 `GIT_CONFIG_GLOBAL=/dev/null`을 기본으로 준다. 작업자의 `engine.py test`(Claude Code sandbox)가 홈을 어떻게 다루는지는 측정하지 않았으므로 적용하지 않았다.
- 2026-11-12, 스킬 실측(Claude Code 2.1.322): 엔진의 `claude_argv`로 리뷰 단계 인자를 그대로 만들어 `hc`로 작업자를 띄웠다. 과제 폴더 `jobs/skill-probe-*`에 빈 제품 저장소를 두고, 작업자에게 스킬 목록을 파일로 옮겨 적고 이름마다 `Skill` 도구를 한 번씩 부르게 했다. 세 번 돌렸다.
  - 지금 설정 그대로: 개인 스킬(`~/.claude/skills`, Matt 포함)과 사용자 설정이 켠 플러그인(pstack)은 목록에 없고 부르면 `Unknown skill`이다. 과제 폴더의 `.claude/skills`와 office 체크아웃의 `.claude/skills`에 둔 스킬은 보이고 불렸다. Claude Code 내장 스킬(`code-review`, `simplify`, `security-review` 등)도 보인다.
  - office 체크아웃의 `.claude/settings.json`에 `enabledPlugins`로 `mattpocock-skills@mattpocock`과 `pstack@cursor-plugins`를 켜면: Matt의 자동 가능 스킬(`mattpocock-skills:tdd`, `:code-review`, `:codebase-design`, `:diagnosing-bugs` 등 11개)과 `pstack:setup-pstack`이 보이고 불렸다. `ask-matt`·`pstack:interrogate`는 목록에 없고, 부르면 `disable-model-invocation` 때문에 쓸 수 없으니 사람에게 실행을 요청하라는 오류가 났다(§3과 같다). 사용자 설정의 `extraKnownMarketplaces`는 읽지 않았지만 켜졌다. 두 플러그인이 이미 플러그인 캐시에 있었다.
  - 함정: 앞 이름 없는 `code-review`는 플러그인을 켜도 Claude Code 내장 `/code-review`로 간다(「Running in the background」로 떴다). Matt 스킬은 `mattpocock-skills:code-review`로 불러야 한다. 리뷰 지시서는 이 이름을 쓴다. 앞 이름 없는 `tdd`·`codebase-design`은 Matt 스킬로 갔다.
  - `Skill` 도구는 리뷰 단계 허용 목록에 없는데도 `--permission-mode dontAsk`에서 돌았다. 허용 목록으로는 단계마다 스킬을 고르지 못한다.
  - 제품 저장소(`repo/.claude/skills`)에 커밋한 스킬은 작업자가 `repo/` 안 파일을 Read한 뒤에도 목록에 없고 `Unknown skill`이었다. PR이 스킬로 리뷰 작업자에게 지시를 넣는 길은 이 시험에서는 열리지 않았다.
  - 확인한 것: mattpocock-skills 1.3.2에는 `hooks/`와 `.mcp.json`이 없고, 두 플러그인의 `SKILL.md`에 `allowed-tools`가 없다. pstack 0.15.36에는 `hooks/`가 없고 `agents/`가 있다. 확인하지 않은 것: 스킬이 자기 폴더의 참고 파일을 Read로 읽을 수 있는지, 스킬 본문의 절차가 단계 허용 목록 안에서 끝까지 도는지, `check_skills.py`.
  - 곁가지: 2.1.322는 단계표의 `Write(<과제>/state.json)` 같은 거부 규칙이 「never matches」라며 `Edit(...)`를 쓰라고 경고한다. 같은 경로의 `Edit(...)` 거부가 함께 있어 빈틈은 아니다. 단계표는 소유자 승인 없이 고치지 않으므로 그대로 둔다.
- 2026-11-12, 리뷰 위임 실측(Claude Code 2.1.322): 공개 장난감 저장소 `minjunkim-dev/office-toy`(시험 뒤 지울 것)의 PR #1(REVIEW.md에 「스킬 위임」 절)을 엔진 리뷰 단계로 돌렸다. 단계표 인자 그대로, 과제를 `review` 단계에 두고 `engine.py run`.
  - 작업자 폴더는 git 저장소가 아니라 `check_skills.py`를 직접 돌리면 git이 실패한다. 그래서 `engine.py skills <과제> <base>`가 `repo/`에서 `--harness claude`로 돌린다. 작업자 sandbox 안에서도 플러그인 캐시를 읽어 종료 코드 0과 `code-review` 경로·sha256(1.3.2, `9c921a8d8813`)을 출력했다.
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
  - 곁가지: 작업자의 git은 사용자 전역 git 설정을 읽는다. `core.hooksPath`가 홈을 가리켜 커밋 때 `post-commit: Operation not permitted`가 나왔다. sandbox가 막았고 커밋은 됐다.

- 2026-11-12, leader부터 리뷰까지 한 번에(Claude Code 2.1.322, 작업자·leader 모두 sonnet): `minjunkim-dev/office-toy`에 `toy-rename`(`mean`을 `arithmetic_mean`으로, 옛 이름 없앰)을 leader 세션에 사람 역할로 주고 끝까지 돌렸다(PR #4, 종료 코드 0).
  - 접수: leader는 등록 전에 이름 후보 세 가지를 물었다. 요청이 막연하지 않다며 `grill-with-docs`는 권하지 않았다. 고른 뒤 `--base origin/main`으로 등록하고 `INTENT.md`의 「하지 않을 것」에 옛 이름을 남기지 않는다고 적었다.
  - 승인 대기: leader가 `blast-radius`를 실행 폴더·금지 폴더와 함께 권했다. 설계 작업자는 Door를 쉬움으로 봤다(외부 호출자 없음). 그래서 `interrogate` 권유와 어려운 PR의 봇 리뷰 요청(§4 남은 결정)은 이번에 돌지 않았다.
  - 구현 작업자는 `mattpocock-skills:tdd`로 빨강(`ImportError`) → 초록. 검사 통과, 엔진이 초안 PR을 열고 Ready를 기다렸다. 사람이 Ready로 바꾼 뒤 리뷰 작업자가 `engine.py skills` 종료 코드 0을 받고 `mattpocock-skills:code-review`를 불렀다. diff가 작아 축별 하위 에이전트는 띄우지 않고 두 축을 직접 봤다. CodeRabbit 건너뜀은 「없음(건너뜀)」으로 판정했다(repo-baseline #129 규칙대로).
  - 함정: 구현·리뷰 작업자 모두 `git grep`이 허용 목록에 없어 거부됐다. 둘 다 파일을 Read로 읽어 대신했다. 옛 이름이 남았는지 같은 확인이 필요한 과제가 잦으면 단계표에 `git -C repo grep *`를 더할지 소유자가 정한다.

## 7. 다시 재현하는 법

토큰을 적게 쓰려면 단계표를 복사해 각 claude 단계에 `"model": "sonnet"`을 넣고 `OFFICE_STAGES`로 지정한다. Haiku는 위 함정 때문에 Bash가 자주 막힌다.

```bash
# 장난감 제품 저장소
mkdir -p /tmp/toy && cd /tmp/toy && git init -q -b main
printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n' > test_calc.py
printf '__pycache__/\n' > .gitignore && git add -A && git commit -qm init

# office 체크아웃 루트에서(이 폴더는 Claude Code에서 한 번 신뢰해 둔다. baseline.path를 먼저 적는다)
python3 pipeline/engine.py new toy-subtract --repo /tmp/toy --test 'python3 -m unittest'
# jobs/toy-subtract/INTENT.md에 목표를 쓴다. 예: calc.py에 subtract(a, b)와 테스트를 더한다
OFFICE_STAGES=/tmp/stages-sonnet.json python3 pipeline/engine.py run toy-subtract   # exit 3: 설계 승인 대기
python3 pipeline/engine.py approve toy-subtract                                       # 사람이 design.md를 읽은 뒤
OFFICE_STAGES=/tmp/stages-sonnet.json python3 pipeline/engine.py run toy-subtract   # exit 4: origin이 GitHub이 아니라 초안 PR을 못 연다. GitHub 저장소면 exit 3: 초안 PR 대기
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
