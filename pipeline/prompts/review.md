# 리뷰 단계

이 세션은 설계·구현 세션과 다른 컨텍스트다(POLICY §3). 구현 대화를 모른다.

- `{baseline}/prompts/review.md`를 읽고 그대로 따른다. repo-baseline 클론은 `{baseline}`이다.
- 리뷰 전 확인은 언제나 `python3 {baseline}/tools/review_preflight.py <PR URL>`로 돌린다. 리뷰할 PR이 repo-baseline 자신의 PR이어도 그 프롬프트의 `git show origin/main:... | python3 -I -` 형태는 쓰지 않는다. `{baseline}`은 PR의 작업 트리(`repo/`)와 다른 체크아웃이다. 리뷰할 PR이 repo-baseline 자신의 PR이면 엔진이 이 단계를 띄우기 전에 `{baseline}` 체크아웃 전체가 `origin/main`과 같은지 확인하고, 다르면 멈춘다. 그래서 PR 쪽 판이 아니다. 리뷰할 PR이 repo-baseline 자신의 PR이면 같은 이유로 `REVIEW.md`도 `{baseline}`의 것을 읽는다. 다른 제품 저장소의 PR이면 `REVIEW.md`는 그 저장소(`repo/`)의 것이다.
- 셸 명령은 허용된 것만, 한 번에 하나씩 쓴다. `&&`·`;`·`|`로 잇지 않는다. 이으면 통째로 거부된다. 먼저 `cd repo`만 따로 한 번 실행한다. 그 뒤 git 읽기와 테스트는 `repo/` 안에서 돌린다. git add·commit·push는 `git -C {job}/repo ...`로만 쓴다.
- 리뷰할 PR: `{job}/pr.txt`에 적힌 URL. 작업 트리 `{job}/repo/`는 이미 그 PR의 브랜치다. `gh pr checkout`은 하지 않는다. 리뷰 전 확인은 `repo/` 안에서 돌린다.
- 고친 커밋은 `git -C {job}/repo push origin HEAD`로만 push한다.
- 보고는 그 프롬프트의 「보고」 형식으로 `{job}/review.md`에 쓴다. 같은 내용을 PR 본문 Review 칸에도 적는다. 고친 본문 전체를 `{job}/pr-body.md`에 쓰고 `gh pr edit <PR URL> --body-file {job}/pr-body.md`로 올린다. 그다음 끝낸다.
- 병합·`main` push·배포는 하지 않는다. 병합은 소유자가 한다(POLICY §5).
- 막히면 지어내지 말고 `{job}/BLOCKED.md`에 이유를 적고 끝낸다.
- 끝낼 때는 `{job}/review.md`나 `{job}/BLOCKED.md` 중 하나를 반드시 쓴다. 앞 라운드에서 이미 끝난 일이어도 이번 결과를 새로 쓴다. 둘 다 없으면 엔진은 실패로 센다.
