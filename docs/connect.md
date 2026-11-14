# 프로젝트 연결

새 프로젝트와 이미 쓰던 프로젝트가 같은 네 단계로 연결한다. 파이프라인은 한 제품을 위해 바뀌지 않는다. 막히면 제품 저장소를 고친다.

## 1. repo-baseline을 적용한다

repo-baseline의 [`prompts/setup.md`](https://github.com/minjunkim-dev/repo-baseline/blob/main/prompts/setup.md)를 코딩 에이전트에게 주고 따르게 한다. 그러면 `AGENTS.md`와 `.github/pull_request_template.md`가 생긴다. 그 프롬프트는 서버 설정을 쓰지 않는다. `main` 브랜치 보호는 소유자가 자기 기기에서 repo-baseline `tools/apply_github_settings.py`로 켠다. 이 스크립트는 그 프롬프트의 2단계가 만든 `.github/repository-settings.json`을 읽는다. 이미 적용한 저장소는 이 단계를 건너뛴다.

## 2. 명령 칸을 채운다

제품 `AGENTS.md`의 `## 명령` 절에 세 줄을 둔다. 엔진은 각 줄의 첫 번째 `` `…` ``를 명령으로 읽고, 뒤의 설명은 읽지 않는다.

```markdown
## 명령

- setup: `<깨끗한 main 체크아웃에서 의존성을 받는 명령>`
- check: `<린터·정적 검사>`
- test: `<테스트>`
```

- 없는 칸은 `` `N/A: <이유>` ``로 쓴다. 엔진은 그 칸을 뺀다. test는 `N/A`일 수 없다.
- setup은 과제를 만들 때 `repo/`(제품의 git worktree)에서 한 번 돈다. 그 뒤 check와 test가 여러 번 돈다.
- 검사 단계는 `check && test`를 `repo/`에서 그대로 돌리고, 그 전에 커밋하지 않은 변경이 없는지 본다. 테스트나 setup이 남기는 파일(캐시, 빌드 결과)은 `.gitignore`에 넣는다.
- 테스트는 작업 트리 안에서 끝낸다. 원본 체크아웃 경로나 홈 폴더의 고정 경로에 쓰지 않는다. 과제마다 worktree가 따로 있어 여러 과제가 함께 돈다.

## 3. doctor를 통과시킨다

office 체크아웃에서 돌린다.

```bash
python3 pipeline/engine.py doctor <제품 저장소 경로>
```

`FAIL`이 하나도 없으면 종료 코드 0이다. 보는 것:

| 줄 | 고치는 법 |
| --- | --- |
| origin has a main branch | `main`을 origin에 push한다 |
| AGENTS.md 「명령」 has setup, check and test cells | 2단계대로 세 줄을 쓴다 |
| PR template on main | `.github/pull_request_template.md`를 둔다(repo-baseline `templates/core/`) |
| gh is logged in | `gh auth login` |
| main is protected | 브랜치 보호나 ruleset을 켠다(repo-baseline `tools/apply_github_settings.py`) |
| a fresh checkout of origin/main | 출력된 git 오류를 고친다(예: 남은 worktree는 `git worktree prune`) |
| setup·check·test passes on main | origin/main의 새 체크아웃에서 그 명령이 통과하게 제품을 고친다 |

doctor는 origin/main을 임시 worktree로 받아 setup → check → test 순서로 돌리고, 끝나면 그 worktree를 지운다.

## 4. 과제를 등록한다

leader가 등록한다(`leader/AGENTS.md`). 손으로 하면 office 체크아웃에서 돌린다.

```bash
python3 pipeline/engine.py new <과제> --repo <제품 저장소 경로> --base origin/main
```

엔진이 `<base>`의 `AGENTS.md`에서 setup·check·test 칸을 읽어 `jobs/<과제>/task.json`에 적고, `repo/`를 만들고, setup을 한 번 돈다. setup이 실패하면 `BLOCKED.md`에 출력을 남긴다. 그 다음은 `jobs/<과제>/INTENT.md`를 채우고 `engine.py run <과제>`이다. 그 뒤 사람이 할 일은 설계 승인과 PR 병합 둘뿐이다. 엔진이 PR을 Ready로 바꾸고, 리뷰 봇에 한 번 요청하고, 다른 세션이 리뷰한 뒤 `jobs/<과제>/REPORT.md`에 결과와 병합 명령을 남긴다. setup이 중간에 끊기면 `BLOCKED.md`가 남는다.
