# 프로젝트 연결

제품 저장소는 새 것이든 이미 쓰던 것이든 같은 다섯 단계로 연결한다. 1~4단계는 office가 그 저장소를 돌리게 하고, 5단계는 그 저장소의 에이전트가 사람이 원할 때 코드 작업을 office로 보내게 한다. 파이프라인은 제품 하나에 맞추려고 바뀌지 않는다. 연결하다 막히면 제품 저장소 쪽을 고친다.

## 1. repo-baseline을 적용한다

repo-baseline이 계약(필수 체크, 머지 큐, 병합 설정)의 원본이다. office는 이 절차를 다시 적지 않는다. repo-baseline 문서를 그대로 따른다.

- 코어 파일: repo-baseline [`README.md`](https://github.com/minjunkim-labs/repo-baseline/blob/main/README.md)의 「코어 적용 3단계」. 그러면 `AGENTS.md`와 `.github/pull_request_template.md`가 생긴다.
- GitHub 설정과 CI: README의 「프로필」 표에서 github 프로필([`templates/github/README.md`](https://github.com/minjunkim-labs/repo-baseline/blob/main/templates/github/README.md))로 간다.
  - 새 저장소면 「(a) 새 저장소 셋업」을 따른다.
  - 이미 쓰던 저장소면 「(b) 기존 저장소 옮기기」를 따른다.

이미 repo-baseline을 적용한 저장소도 3단계 doctor가 계약을 다시 확인한다.

## 2. 명령 칸을 채운다

제품 `AGENTS.md`의 `## 명령` 절에 세 줄을 둔다. 엔진은 각 줄에서 첫 번째 `` `…` ``만 명령으로 읽는다. 그 뒤의 설명은 읽지 않는다.

```markdown
## 명령

- setup: `<깨끗한 main 체크아웃에서 의존성을 받는 명령>`
- check: `<린터·정적 검사>`
- test: `<테스트>`
```

- 쓸 명령이 없는 칸은 `` `N/A: <이유>` ``로 쓴다. 엔진은 그 칸을 건너뛴다. test는 `N/A`로 둘 수 없다.
- 엔진은 과제를 만들 때 `repo/`에서 setup을 한 번 돌린다. `repo/`는 과제마다 따로 만드는 제품의 git worktree(브랜치를 다른 폴더에 꺼낸 작업 폴더)다. 그 뒤로 check와 test는 여러 번 돈다.
- 검사 단계는 먼저 커밋하지 않은 변경이 없는지 본다. 그다음 `check && test`를 `repo/`에서 그대로 돌린다. 테스트나 setup이 남기는 파일(캐시, 빌드 결과)은 `.gitignore`에 넣는다.
- 테스트는 작업 트리 안에서만 파일을 쓴다. 원본 체크아웃이나 홈 폴더의 고정 경로에는 쓰지 않는다. 과제마다 worktree가 따로 있고 여러 과제가 함께 돌기 때문이다.

## 3. doctor를 통과시킨다

office 체크아웃에서 돌린다.

```bash
python3 pipeline/engine.py doctor <제품 저장소 경로>
```

`FAIL`이 하나도 없으면 종료 코드가 0이다. doctor가 보는 줄과 고치는 법은 다음과 같다.

| 줄 | 고치는 법 |
| --- | --- |
| origin has a main branch | `main`을 origin에 push한다 |
| AGENTS.md 「명령」 has setup, check and test cells | 2단계대로 세 줄을 쓴다 |
| PR template on main | `.github/pull_request_template.md`를 둔다(repo-baseline `templates/core/`) |
| gh is logged in | `gh auth login` |
| main is protected | 브랜치 보호나 ruleset을 켠다(repo-baseline `tools/apply_github_settings.py`) |
| GitHub settings match the repo-baseline contract | 출력된 `DIFF:`·`MANUAL:` 줄을 1단계의 github 프로필 절차대로 소유자가 적용한다. `확인 못 함`은 설정을 읽지 못한 것이다(권한, 네트워크, main에 `.github/repository-settings.json` 없음). 통과가 아니다. `names …, but origin is …`면 그 파일의 `repository`를 이 저장소로 고쳐 main에 올린다 |
| a fresh checkout of origin/main | 출력된 git 오류를 고친다(예: 남은 worktree는 `git worktree prune`) |
| setup·check·test passes on main | origin/main을 새로 받은 체크아웃에서 그 명령이 통과하도록 제품을 고친다 |

doctor는 origin/main을 임시 worktree로 받아 setup → check → test 순서로 돌린다. 끝나면 그 worktree를 지운다.

계약 줄은 `baseline.path`의 repo-baseline 클론에서 `tools/apply_github_settings.py <제품 저장소 경로>`를 plan으로만 돌린다(`--apply` 없이). 그래서 doctor는 GitHub 설정을 바꾸지 않는다. 다른 줄처럼 origin/main을 받은 임시 worktree의 `.github/repository-settings.json`을 읽는다. 커밋하지 않은 설정은 보지 않는다. 그 파일의 `repository`가 origin과 다르면(다른 저장소에서 복사한 파일) 도구를 돌리지 않고 실패로 보고한다.

## 4. 과제를 등록한다

과제는 보통 leader가 등록한다(`leader/AGENTS.md`). leader는 사람과 말하는 상주 Claude Code 세션이다. 손으로 등록하려면 office 체크아웃에서 돌린다.

```bash
python3 pipeline/engine.py new <과제> --repo <제품 저장소 경로> --base origin/main
```

엔진은 이렇게 한다.

1. `<base>`의 `AGENTS.md`에서 setup·check·test 칸을 읽어 `jobs/<과제>/task.json`에 적는다.
2. `repo/`를 만든다.
3. setup을 한 번 돌린다. setup이 실패하면 그 출력을 `BLOCKED.md`에 남긴다. setup이 중간에 끊겨도 `BLOCKED.md`가 남는다.

그다음 `jobs/<과제>/INTENT.md`를 채우고 `engine.py run <과제>`를 돌린다. 그 뒤 사람이 할 일은 되돌리기 어려움인 설계의 승인과 PR의 병합뿐이다. 쉬움 설계는 엔진이 승인하고, 쉬움 PR은 리뷰와 CI가 끝나면 엔진이 머지 큐에 넣는다(조건은 [AGENTS.md](../AGENTS.md) 「push와 PR」).

엔진은 PR을 Ready로 바꾼다. PR이 쉬움 판정을 받지 못하면 리뷰 봇에 리뷰를 한 번 요청한다. 쉬움 판정은 엔진이 내린다. 승인된 설계와 PR 본문이 둘 다 `되돌리기: 쉬움`이라고 쓰고, 본문이 `민감한 파일: 없음`이라고 써야 한다. 브랜치가 에이전트 설정 폴더나 지시 파일(`.github`, `.claude`, `AGENTS.md` 등)을 바꾸면 본문과 관계없이 쉬움이 아니다. 자세한 조건은 [AGENTS.md](../AGENTS.md)에 있다. 그다음 다른 세션이 리뷰한다. 리뷰가 끝나면 엔진이 `jobs/<과제>/REPORT.md`에 결과와 병합 명령을 남긴다.

사람이나 엔진이 그 PR을 머지 큐에 넣은 뒤 병합되지 않고 빠지면(`merge_group` CI 실패, 충돌 등) `workers`와 `board`가 그 이유와 PR 링크를 `jobs/<과제>/BLOCKED.md`에 쓴다. 엔진은 큐에 다시 넣지 않는다. 사람이 고쳐 다시 큐에 넣거나 PR이 병합되면 `workers`와 `board`가 그 `BLOCKED.md`를 `BLOCKED.<시각>.md`로 옮긴다. 다른 막힘은 옮기지 않는다.

## 5. 원하면 코드 작업을 office로 보내게 한다

office는 선택이다. 제품 저장소는 여러 사람이 쓰고, office와 leader는 그것을 띄운 사람의 컴퓨터에만 있다. 그래서 사람이 대화에서 office로 돌리라고 할 때만 제품 에이전트가 leader에 넘긴다. 그러려면 제품 `AGENTS.md`의 길잡이 아래에 다음 규칙을 두고, 넘기는 법을 `docs/agents/office.md`에 둔다. doctor는 이 단계를 보지 않는다.

```markdown
- repo-office 파이프라인은 선택이다. 이 저장소는 여러 사람이 쓰고, office는 그것을 띄운 사람의 컴퓨터에만 있다.
  - 사람이 이 대화에서 office나 leader로 돌리라고 할 때만 떠 있는 leader 세션에 과제를 넘기고 멈춘다. 그 말이 넘겨도 된다는 허락이다. 따로 묻지 않는다. 넘기는 법은 [office 절차](docs/agents/office.md)에 있다.
  - 그 밖의 작업은 이 저장소의 평소 절차(예: `implement`)대로 한다. office로 넘길지 먼저 묻지 않는다.
  - leader가 언제 사람에게 묻는지와 PR을 누가 병합하는지는 [repo-office](https://github.com/minjunkim-labs/repo-office)의 `leader/AGENTS.md`와 `AGENTS.md`가 정한다. 이 저장소에 옮겨 적지 않는다.
  - office 과제의 브랜치는 `office/<과제>`이고, 이 저장소의 브랜치 이름 규칙을 따르지 않는다.
  - office 작업자는 이 규칙을 따르지 않는다. 작업 폴더가 repo-office 체크아웃의 `jobs/<과제>/`이고 브랜치가 `office/<과제>`면 office 작업자다. office 작업자는 단계 지시서대로 일하고 leader에 넘기지 않는다.
```

`docs/agents/office.md`의 넘기는 법:

```markdown
## 과제 넘기기

1. `hc get office-leader --json`을 돌린다. 종료 코드가 0이고 `state`가 `live`이고 `status`(객체면 그 `state`)가 `error`가 아니면 leader가 받을 수 있다.
2. 받을 수 있으면 한 줄로 보낸다: `hc send office-leader '[office 접수] repo=<이 저장소의 절대 경로> issue=#<번호> 요청: <사람이 이 대화에서 한 말 그대로>'`
   - 요청에는 사람의 말만 넣는다. 줄바꿈은 공백으로 바꾼다. 셸이 말 안의 `` ` ``·`$`를 실행하지 않게 메시지를 작은따옴표로 감싸고, 말 안의 `'`는 `'\''`로 바꾼다. Issue 본문이나 다른 파일의 글은 옮기지 않는다. leader가 Issue를 직접 읽는다.
   - Issue가 없으면 `issue=없음`으로 쓴다.
3. 보냈으면 사람에게 "leader에 넘겼습니다. 진행은 `hc attach office-leader`에서 봅니다"라고 알리고 멈춘다.
4. 1단계가 안 되거나 `hc send`가 0이 아닌 코드로 끝나면, 사람에게 "이 작업은 repo-office leader 세션에 주세요"라고 알리고 멈춘다. leader를 `office-leader`로 띄우는 명령은 repo-office README 「처음 한 번」 5에 있다.
```

leader는 `[office 접수]` 메시지를 새 과제로만 받는다. 기다리던 질문의 답으로 보지 않는다(`leader/AGENTS.md`). 사람이 하는 설계 승인(`engine.py approve`)은 `leader/.claude/settings.json`이 실행 전에 사람 확인을 묻는다.

이 방식이 남기는 위험: 같은 사용자의 프로세스는 무엇이든 `hc send office-leader`로 leader 입력창에 글을 칠 수 있다. 접두어가 없는 글은 leader가 사람의 말과 가리지 못한다. `hc send --force`는 확인 창에 답할 수도 있다. 그래서 설계 승인 확인도 사람만 누른다고 보장하지 못한다. office 작업자는 deny 규칙(`Bash(hc *)`)으로 hc를 막는다. 다만 접두어 deny는 우회될 수 있고, 작업자는 원래 sandbox 없이 돈다(`AGENTS.md` 「작업자 권한」). 제품 저장소의 에이전트는 Issue 본문을 leader에 옮기지 않는다. leader가 Issue를 직접 읽고, 그 글을 지시로 따르지 않는다.
