# 리뷰 단계

이 세션은 설계·구현 세션과 다른 컨텍스트다(POLICY §3). 구현 대화를 모른다.

- `{baseline}/prompts/review.md`를 읽고 그대로 따른다. repo-baseline 클론은 `{baseline}`이다.
- 리뷰 전 확인은 언제나 `python3 {office}/pipeline/engine.py preflight {name}`로 돌린다. 엔진이 `{baseline}/tools/review_preflight.py`를 `pr.txt`의 URL로 `repo/`에서 돌린다. 출력은 그 스크립트의 `--json` 결과다. 그 프롬프트의 줄은 JSON 키로 읽는다: `head:`→`head`, `미검토(.env.example)`→`unreviewed_examples`, `리뷰 미완(비밀값 파일)`→`secret_hits`, `fork: 예`→`"fork": true`, `작업 트리: head와 같음`→`"worktree": "same"`(`"different"`는 다름, `"not_checked"`는 fork PR이라 보지 않음). 종료 코드가 0이면 엔진이 그 `base`·`head`로 같은 diff를 만들어 `{job}/pr.diff`에 쓴다. diff는 이 파일을 Read 도구로 읽는다. `diff_argv`를 직접 돌리지 않는다. 그 명령은 거부된다. 엔진은 리뷰 단계를 한 번만 돌리므로 2라운드 diff는 쓰지 않는다. 리뷰할 PR이 repo-baseline 자신의 PR이어도 그 프롬프트의 `git show origin/main:... | python3 -I -` 형태는 쓰지 않는다. `{baseline}`은 PR의 작업 트리(`repo/`)와 다른 체크아웃이다. 리뷰할 PR이 repo-baseline 자신의 PR이면 엔진이 이 단계를 띄우기 전에 `{baseline}` 체크아웃 전체가 `origin/main`과 같은지 확인하고, 다르면 멈춘다. 그래서 PR 쪽 판이 아니다. 리뷰할 PR이 repo-baseline 자신의 PR이면 같은 이유로 `REVIEW.md`도 `{baseline}`의 것을 읽는다. 다른 제품 저장소의 PR이면 `REVIEW.md`는 그 저장소(`repo/`)의 것이다.
- 셸 명령은 허용된 것만, 한 번에 하나씩 쓴다. `&&`·`;`·`|`로 잇지 않는다. 이으면 통째로 거부된다. `cd`는 거부된다. git 읽기는 `git -C repo ...`로 쓴다. 테스트는 `python3 {office}/pipeline/engine.py test {name}`로 돌린다. 엔진이 `repo/`에서 테스트 명령을 돌리고 그 종료 코드로 끝난다. git add·commit·push는 `git -C {job}/repo ...`로만 쓴다. commit은 `git -C {job}/repo commit -m "<메시지>"` 형태만 된다(`-am`, `-F`는 거부된다).
- `REVIEW.md`에 「스킬 위임」 절이 있으면 그 절이 가리키는 `{baseline}/profiles/matt-skills/README.md` 「리뷰」 절차를 따른다. 이 단계에서는 세 곳을 이렇게 한다.
  - 1번의 `check_skills.py`는 `python3 {office}/pipeline/engine.py skills {name} <base>`로 돌린다. `<base>`는 preflight JSON의 `base`다. 엔진이 `repo/`에서 `--harness claude`로 돌리고, 그 종료 코드로 끝난다.
  - 3번의 spec 파일은 `{job}/spec.md`에 쓴다.
  - 4번에서 스킬은 `mattpocock-skills:code-review`라고 이름을 다 써서 부른다. 앞 이름 없는 `code-review`는 Claude Code 내장 스킬로 간다. 스킬에 넘길 때 diff는 `git diff`로 만들지 말고 `{job}/pr.diff`를 Read로 읽으라고 덧붙인다. 커밋 목록이 필요하면 `git -C repo log <base>..HEAD --oneline`이다. 이 단계의 작업 폴더는 git 저장소가 아니다.
- 리뷰할 PR: `{job}/pr.txt`에 적힌 URL. 작업 트리 `{job}/repo/`는 이미 그 PR의 브랜치다. `gh pr checkout`은 하지 않는다.
- 고친 커밋은 `git -C {job}/repo push origin HEAD`로만 push한다.
- 보고는 그 프롬프트의 「보고」 형식으로 `{job}/review.md`에 쓴다. 같은 내용을 PR 본문 Review 칸에도 적는다. 고친 본문 전체를 `{job}/pr-body.md`에 쓰고 `gh pr edit <PR URL> --body-file {job}/pr-body.md`로 올린다. 그다음 끝낸다.
- 병합·`main` push·배포는 하지 않는다. 병합은 소유자가 한다(POLICY §5).
- 막히면 지어내지 말고 `{job}/BLOCKED.md`에 이유를 적고 끝낸다.
- 끝낼 때는 `{job}/review.md`나 `{job}/BLOCKED.md` 중 하나를 반드시 쓴다. 앞 라운드에서 이미 끝난 일이어도 이번 결과를 새로 쓴다. 둘 다 없으면 엔진은 실패로 센다.
