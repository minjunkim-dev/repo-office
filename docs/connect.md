# 프로젝트 연결

제품 저장소는 새 것이든 이미 쓰던 것이든 같은 네 단계로 연결한다. 파이프라인은 제품 하나에 맞추려고 바뀌지 않는다. 연결하다 막히면 제품 저장소 쪽을 고친다.

## 1. repo-baseline을 적용한다

repo-baseline의 [`prompts/setup.md`](https://github.com/minjunkim-dev/repo-baseline/blob/main/prompts/setup.md)를 코딩 에이전트에게 주고 따르게 한다. 그러면 `AGENTS.md`와 `.github/pull_request_template.md`가 생긴다.

이 프롬프트는 GitHub 서버 설정을 바꾸지 않는다. `main` 브랜치 보호는 소유자가 자기 기기에서 repo-baseline의 `tools/apply_github_settings.py`로 켠다. 이 스크립트는 프롬프트의 2단계가 만든 `.github/repository-settings.json`을 읽는다.

이미 repo-baseline을 적용한 저장소는 이 단계를 건너뛴다.

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
| a fresh checkout of origin/main | 출력된 git 오류를 고친다(예: 남은 worktree는 `git worktree prune`) |
| setup·check·test passes on main | origin/main을 새로 받은 체크아웃에서 그 명령이 통과하도록 제품을 고친다 |

doctor는 origin/main을 임시 worktree로 받아 setup → check → test 순서로 돌린다. 끝나면 그 worktree를 지운다.

## 4. 과제를 등록한다

과제는 보통 leader가 등록한다(`leader/AGENTS.md`). leader는 사람과 말하는 상주 Claude Code 세션이다. 손으로 등록하려면 office 체크아웃에서 돌린다.

```bash
python3 pipeline/engine.py new <과제> --repo <제품 저장소 경로> --base origin/main
```

엔진은 이렇게 한다.

1. `<base>`의 `AGENTS.md`에서 setup·check·test 칸을 읽어 `jobs/<과제>/task.json`에 적는다.
2. `repo/`를 만든다.
3. setup을 한 번 돌린다. setup이 실패하면 그 출력을 `BLOCKED.md`에 남긴다. setup이 중간에 끊겨도 `BLOCKED.md`가 남는다.

그다음 `jobs/<과제>/INTENT.md`를 채우고 `engine.py run <과제>`를 돌린다. 그 뒤 사람이 할 일은 설계 승인과 PR 병합 둘뿐이다.

엔진은 PR을 Ready로 바꾼다. PR이 쉬움 판정을 받지 못하면 리뷰 봇에 리뷰를 한 번 요청한다. 쉬움 판정은 사람이 승인한 설계와 PR 본문이 둘 다 되돌리기 쉽다고 쓰고, PR이 민감한 파일을 건드리지 않을 때 엔진이 내린다(자세한 조건은 [AGENTS.md](../AGENTS.md)). 그다음 다른 세션이 리뷰한다. 리뷰가 끝나면 엔진이 `jobs/<과제>/REPORT.md`에 결과와 병합 명령을 남긴다.
