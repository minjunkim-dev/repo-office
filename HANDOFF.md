# 이어받기

이 문서는 다른 사람·계정·에이전트가 claude-office 작업을 바로 이어받기 위한 기록이다. 2026-11-10에 PR #10과 함께 썼다. 남은 일은 GitHub 이슈에 있고, 이 문서는 그 배경과 함정을 적는다.

> 2026-11-11: 이 저장소는 repo-baseline-office의 `claude-office/`를 git 기록째 옮긴 것이다. 아래의 `claude-office/`는 이 저장소 루트다. 원본과 다섯 곳을 맞추는 일(§1, §6)은 옛 배치의 일이라 이제 하지 않는다.

## 1. 지금 상태

- PR #10은 2026-11-10에 `main`으로 병합됐다.
  - 기준 파일을 원본 `minjunkim-dev/repo-baseline`의 `c3f6d56`과 같게 맞췄다.
  - `claude-office/`를 단계 파이프라인으로 다시 지었다.
- `claude-office/` 밖에서 원본과 다른 곳은 다섯 곳뿐이다. 목록은 루트 README 머리말에 있다.
- 다섯 곳이 그대로인지는 다음 명령으로 확인한다. `upstream/main`은 원본의 `main`을 받아 둔 ref다.

  ```bash
  git fetch https://github.com/minjunkim-dev/repo-baseline main:refs/remotes/upstream/main
  git diff upstream/main --stat -- . ':!claude-office'
  ```

- 검사는 루트 `AGENTS.md`의 test 명령이다. 마지막 실행은 exit 0이었다.

## 2. 남은 일(이슈)

#11(PR #10 리뷰·병합), #12(리뷰 단계를 실제 PR로 검증), #13(leader 세션으로 실제 제품 과제 하나 끝까지), #14(동시 실행, 긴 단계, 엔진 재시작), #15(OS 샌드박스 채택), #29(검사 단계 sandbox)는 끝났다. 열린 이슈는 없다. 다음 큰 일(스킬을 단계에 연결하기)과 소유자가 정할 것은 [HARNESS.md](HARNESS.md)에 있다. #16(영상에서 뺀 기능: 알림, 다른 모델, 동시 상한, 시작 훅)은 2026-11-11에 지금 필요 없다고 보고 닫았다. 필요해지면 새 이슈로 연다.


## 3. 왜 이렇게 지었나

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

## 4. 실제로 돌려서 알게 된 함정

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
  - `denyWrite`에 office 체크아웃 전체를 넣으면 그 안의 과제 폴더를 `allowWrite`로 열어도 쓰기가 막혔다. 그래서 체크아웃의 항목 중 `claude-office/jobs`만 빼고 하나씩 막는다.
  - 네트워크: `allowedDomains: ["github.com"]`으로 `git -C repo fetch origin`이 됐다. `gh`는 sandbox 안에서 `~/.config/gh`를 읽지 못하고, 읽게 해도 macOS 인증서 확인(TLS)에서 실패하고, 그것을 풀어도(`enableWeakerNetworkIsolation`) 키체인의 토큰을 읽지 못해 401이었다. 키체인을 sandbox에 열면 작업자 코드도 키체인을 읽게 되므로 열지 않는다. 대신 `gh`·`preflight`·`push`를 `excludedCommands`로 뺐고, 그러자 `gh pr view`가 됐다.
  - 고친 엔진으로 장난감 과제를 설계·구현·검사까지 돌려 PR 대기(exit 3)까지 갔다. 리뷰 단계는 실제 PR로 돌리지 않았다. 리뷰 단계의 명령은 위 `gh`·`fetch` 시험으로만 확인했다.
  - PR #28 리뷰가 찾은 것: 과제 폴더 전체를 열면 sandbox 안의 테스트가 `task.json`·`state.json`·`pr.txt`를 고친다(Edit 거부 규칙은 도구에만 듣는다). 제품 저장소의 git 데이터를 열면 `repo/.git` 파일, `config`, `hooks`도 고친다. 그 설정은 sandbox 밖에서 도는 preflight·push의 git이 따른다(`core.fsmonitor`, `pre-push`). 그래서 이 경로들과 아직 없는 지시 파일(`CLAUDE.md` 등)을 하나씩 `denyWrite`에 넣었다. 같은 날 실제 작업자로 13곳에 쓰기를 시도해, 과제 폴더의 일반 파일만 되고 제어 파일·git 설정·hook·worktree 연결 파일·새 `CLAUDE.md`·`pr.txt` 생성은 모두 `Operation not permitted`인 것을 봤다. 그 뒤에도 `git commit`은 됐다. 리뷰 2라운드는 과제 코드가 `repo/` 안에 하위 저장소(gitlink)를 커밋하고 그 gitdir 설정에 `core.fsmonitor`를 넣으면 sandbox 밖의 `git status`가 그 명령을 실행하는 것을 찾았다(git 2.54.0, sandbox 없이 재현). 파일을 하나씩 막는 대신, 엔진이 돌리는 git에 환경 변수(`GIT_CONFIG_COUNT`)로 `core.fsmonitor=false` 등을 강제했다. 하위 저장소의 git에도 넘어간다. 단위 테스트가 이 공격을 재현하고 강제가 막는 것을 확인한다.
  - 남은 구멍은 `claude-office/AGENTS.md` 규칙에 적었다(단계 도중 새로 생긴 과제·worktree 폴더, `github.com`으로 내보내기, 리뷰 `push`에는 git 설정 강제가 없음). Linux는 bubblewrap과 socat이 있어야 하고 시험하지 않았다. Windows는 sandbox가 없으므로 작업자가 시작하지 않는다.
- #29(2026-11-11): 검사 단계는 엔진이 sandbox 없이 테스트를 돌려, 작업자가 쓴 코드가 사용자 권한으로 한 번 실행됐다. 그래서 엔진이 macOS `sandbox-exec`로 감싼다. Claude Code가 쓰는 sandbox runtime(`srt`)은 설치되어 있지 않아 의존성을 늘리지 않으려고 쓰지 않았다. 작업자의 `engine.py test`는 감싸지 않는다. Claude Code sandbox 안에서는 sandbox를 다시 걸 수 없다. 단위 테스트가 실제 `sandbox-exec`로 과제 폴더 쓰기와 `$TMPDIR` 쓰기는 되고, 밖의 파일·`task.json`·`pr.txt` 생성·`repo/.git`·새 `CLAUDE.md`·홈 폴더 읽기는 막히는 것을 확인한다. 같은 날 엔진으로 장난감 과제를 돌려 검사 단계가 sandbox 안에서 테스트 2개를 통과(exit 0)했고, 그 과제 폴더에서 `https://github.com` 연결, 홈 폴더 쓰기, `~/.zshrc` 읽기가 모두 `Operation not permitted`인 것을 봤다. 엔진의 `git rev-parse`·`git status`는 sandbox 밖이지만 `SAFE_GIT`을 쓴다. BGLike(Godot)의 `make godot-check`가 sandbox 안에서 홈 쓰기(`~/Library/Application Support/Godot`)와 `~/.gitconfig` 읽기로 실패해서, 검사 단계는 `HOME=<과제>/tmp/home`과 `GIT_CONFIG_GLOBAL=/dev/null`을 기본으로 준다. 작업자의 `engine.py test`(Claude Code sandbox)가 홈을 어떻게 다루는지는 측정하지 않았으므로 적용하지 않았다.
- 2026-11-12, 스킬 실측(HARNESS §9의 1, Claude Code 2.1.322): 엔진의 `claude_argv`로 리뷰 단계 인자를 그대로 만들어 `hc`로 작업자를 띄웠다. 과제 폴더 `jobs/skill-probe-*`에 빈 제품 저장소를 두고, 작업자에게 스킬 목록을 파일로 옮겨 적고 이름마다 `Skill` 도구를 한 번씩 부르게 했다. 세 번 돌렸다.
  - 지금 설정 그대로: 개인 스킬(`~/.claude/skills`, Matt 포함)과 사용자 설정이 켠 플러그인(pstack)은 목록에 없고 부르면 `Unknown skill`이다. 과제 폴더의 `.claude/skills`와 office 체크아웃의 `.claude/skills`에 둔 스킬은 보이고 불렸다. Claude Code 내장 스킬(`code-review`, `simplify`, `security-review` 등)도 보인다.
  - office 체크아웃의 `.claude/settings.json`에 `enabledPlugins`로 `mattpocock-skills@mattpocock`과 `pstack@cursor-plugins`를 켜면: Matt의 자동 가능 스킬(`mattpocock-skills:tdd`, `:code-review`, `:codebase-design`, `:diagnosing-bugs` 등 11개)과 `pstack:setup-pstack`이 보이고 불렸다. `ask-matt`·`pstack:interrogate`는 목록에 없고, 부르면 `disable-model-invocation` 때문에 쓸 수 없으니 사람에게 실행을 요청하라는 오류가 났다(HARNESS §5와 같다). 사용자 설정의 `extraKnownMarketplaces`는 읽지 않았지만 켜졌다. 두 플러그인이 이미 플러그인 캐시에 있었다.
  - 함정: 앞 이름 없는 `code-review`는 플러그인을 켜도 Claude Code 내장 `/code-review`로 간다(「Running in the background」로 떴다). Matt 스킬은 `mattpocock-skills:code-review`로 불러야 한다. 리뷰 위임(HARNESS §9의 2)에서 지시서와 「스킬 위임」 절이 쓰는 이름을 확인한다. 앞 이름 없는 `tdd`·`codebase-design`은 Matt 스킬로 갔다.
  - `Skill` 도구는 리뷰 단계 허용 목록에 없는데도 `--permission-mode dontAsk`에서 돌았다. 허용 목록으로는 단계마다 스킬을 고르지 못한다.
  - 제품 저장소(`repo/.claude/skills`)에 커밋한 스킬은 작업자가 `repo/` 안 파일을 Read한 뒤에도 목록에 없고 `Unknown skill`이었다. PR이 스킬로 리뷰 작업자에게 지시를 넣는 길은 이 시험에서는 열리지 않았다.
  - 확인한 것: mattpocock-skills 1.3.2에는 `hooks/`와 `.mcp.json`이 없고, 두 플러그인의 `SKILL.md`에 `allowed-tools`가 없다. pstack 0.15.36에는 `hooks/`가 없고 `agents/`가 있다. 확인하지 않은 것: 스킬이 자기 폴더의 참고 파일을 Read로 읽을 수 있는지, 스킬 본문의 절차가 단계 허용 목록 안에서 끝까지 도는지, `check_skills.py`.
  - 곁가지: 2.1.322는 단계표의 `Write(<과제>/state.json)` 같은 거부 규칙이 「never matches」라며 `Edit(...)`를 쓰라고 경고한다. 같은 경로의 `Edit(...)` 거부가 함께 있어 빈틈은 아니다. 단계표는 소유자 승인 없이 고치지 않으므로 그대로 둔다.
- 2026-11-12, 리뷰 위임 실측(HARNESS §9의 2, Claude Code 2.1.322): 공개 장난감 저장소 `minjunkim-dev/office-toy`(시험 뒤 지울 것)의 PR #1(REVIEW.md에 「스킬 위임」 절)을 엔진 리뷰 단계로 돌렸다. 단계표 인자 그대로, 과제를 `review` 단계에 두고 `engine.py run`.
  - 작업자 폴더는 git 저장소가 아니라 `check_skills.py`를 직접 돌리면 git이 실패한다. 그래서 `engine.py skills <과제> <base>`가 `repo/`에서 `--harness claude`로 돌린다. 작업자 sandbox 안에서도 플러그인 캐시를 읽어 종료 코드 0과 `code-review` 경로·sha256(1.3.2, `9c921a8d8813`)을 출력했다.
  - 판은 고정되지 않는다. office `.claude/settings.json`은 `enabledPlugins`만 둔다. 같은 이름 `mattpocock` 마켓플레이스가 사용자 범위(`~/.claude/plugins/known_marketplaces.json`)에 ref 없이 있으면 프로젝트 설정의 ref는 듣지 않는다(PR #4 리뷰). 이 실측에 실린 것은 캐시 폴더 이름이 `1.3.2`여도 upstream `main`의 `4ae2317`이다(태그 `v1.3.2`는 `d1478fc`, `code-review/`는 두 커밋 사이에 같다). 판을 고정하려면 실린 `gitCommitSha`로 확인한다. 남은 이슈다.
  - 작업자는 `mattpocock-skills:code-review`를 불렀고, 스킬이 두 축을 하위 에이전트로 띄웠다. `Agent` 도구도 허용 규칙 없이 `dontAsk`에서 돌았다. 하위 에이전트는 지시대로 `pr.diff`를 Read로 읽었고, `cd ... && python3 -m unittest`는 거부됐다(부모의 `engine.py test` 결과로 대신했다). 보고를 `review.md`와 PR 본문에 쓰고 엔진은 `done`으로 끝났다.
  - 함정: 작업자의 `engine.py test`가 `repo/`에 `__pycache__/`를 남겨, 보고 직전 preflight가 `worktree: different`를 냈다. 작업자는 원인을 확인하고 넘어갔다. 제품 저장소의 `.gitignore`에 없으면 매번 나온다.
  - 첫 실행은 작업자 CLI 계정의 5시간 한도에 걸려 첫 응답 전에 끝났고, 엔진은 「review.md 없음」 두 번으로 `BLOCKED.md`를 썼다. 한도 오류와 작업 실패를 구별하지 못한다.
- 2026-11-12, leader 수동 스킬 요청 실측(HARNESS §9의 4, Claude Code 2.1.322, Opus 5.5 medium): `hc`로 `leader/`에 대화형 Claude Code를 띄우고, 장난감 저장소에 막연한 요청("계산 기능을 좀 더, 뭘 넣을지는 모르겠다")을 줬다.
  - 처음 규칙("요청이 막연하면 `grill-with-docs`를 권한다")으로는 leader가 방향 세 가지를 묻고, 과제를 등록하고, 엔진을 돌린 뒤에야 "다음에는 `grill-with-docs`로" 권했다. 그래서 규칙에 "방향을 묻기 전에, 등록하기 전에 권한다"를 더했다. 고친 뒤 새 leader 세션은 방향 질문 안에 `grill-with-docs` 권유를 함께 넣었다.
  - 설계 승인 대기(종료 코드 3): 작업자 설계의 Door가 되돌리기 쉬움이라 leader가 "`interrogate`·`blast-radius`는 권하지 않는다"고 밝히고 승인을 요청했다. Door가 어려움인 설계와 병합 뒤 `retro` 권유는 돌려 보지 않았다.
- 2026-11-12, 설계·구현 스킬 실측(HARNESS §9의 3, Claude Code 2.1.322): 로컬 장난감 과제 `toy-tdd`(calc.py에 `multiply`)를 설계 → 승인 → 구현 → 검사까지 엔진으로 돌렸다.
  - 설계 작업자는 지시대로 `design.md`에 「테스트할 seam」 목록(공개 인터페이스 하나, 경우 다섯)을 적었다. `codebase-design`은 부르지 않았다(인터페이스가 정해진 과제라 지시서상 부를 때가 아니다).
  - 구현 작업자는 `mattpocock-skills:tdd`를 불렀고, 테스트를 먼저 쓰고 `engine.py test`로 실패를 본 뒤 구현해 통과시켰다(빨강 → 초록). seam을 사용자에게 묻지 않고 승인된 목록을 따랐다. 커밋 하나, 테스트 5개 통과.
  - 함정: 작업자의 `engine.py test`가 남긴 `__pycache__/`를 검사 단계가 커밋하지 않은 변경으로 보아 두 번 실패했다. 작업자의 `rm`은 거부되어 `BLOCKED.md`를 썼다. 그래서 엔진이 돌리는 테스트에 `PYTHONDONTWRITEBYTECODE=1`을 준다. Python 말고 다른 도구의 산출물은 제품 저장소의 `.gitignore`에 있어야 한다. 고친 뒤 캐시를 지우고 검사 단계만 다시 돌려 통과(`pr.txt` 대기)를 봤다. 구현 단계부터 다시 돌리지는 않았다.

## 5. 다시 재현하는 법

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

## 6. 이어받을 때 주의

- 정본은 원본 `minjunkim-dev/repo-baseline`이다. 소비 레포 셋업과 `profiles/hash-copy/apply_baseline.py`는 원본에서 실행한다.
- 원본을 다시 맞출 때는 원본 `main`으로 `claude-office/` 밖을 통째로 바꾼다. 그다음 README 머리말의 다섯 곳만 다시 더한다.
- `gh`는 이 체크아웃에서 원본 저장소를 기본으로 잡을 수 있다. PR·이슈 명령에는 `--repo minjunkim-dev/repo-office`를 붙인다.
- 원본 `tools/review_preflight.py`에 자동 보안 리뷰가 지적한 셸 인용 문제가 하나 있다. 이 저장소에서는 원본과 같게 두므로 원본에서 고친다. 보안 지적이므로 공개 이슈가 아니라 비공개 경로로 알린다(POLICY §7).
