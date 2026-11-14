# 구현 단계

이 단계 하나만 한다. 제품 코드는 `repo/`에 있다(작업 폴더는 과제 폴더다). push·`gh pr merge`·`gh pr edit`·`gh api` 쓰기는 거부된다. 엔진이 이 단계를 다시 띄웠으면 `{job}/INTENT.md`와 이 지시서를 다시 읽는다. leader가 막힌 일의 답을 `INTENT.md` 결정 기록에 적었을 수 있다. 테스트는 `python3 {office}/pipeline/engine.py test {name}`로 돌린다. 엔진이 `repo/`에서 테스트 명령을 돌리고 그 종료 코드로 끝난다. 포그라운드로 돌리고(Bash timeout 600000, 백그라운드 실행을 쓰지 않는다) 결과를 읽은 뒤 다음으로 간다. 엔진은 작업자의 턴이 끝나면 세션을 닫는다. 백그라운드로 돌려 두고 턴을 끝내면 결과를 받지 못하고 이 단계는 실패한다. commit은 `git -C {job}/repo commit -m "<메시지>"` 형태로 한다(`-am`, `-F`는 쓰지 않는다). 사람이 승인한 `{job}/design.md` 그대로 구현한다.

- 작업할 곳: `{job}/repo/` (브랜치 `{branch}`).
- 설계에 없는 변경을 더하지 않는다. 설계를 바꿔야 하면 구현하지 말고 `{job}/BLOCKED.md`에 적고 끝낸다.
- `mattpocock-skills:tdd` 스킬을 불러 빨강→초록으로 구현한다. 그 스킬이 seam을 사용자와 확인하라고 하면, 승인된 `{job}/design.md`의 「테스트할 seam」 목록으로 대신한다. 목록 밖 seam이 필요하면 구현하지 말고 `{job}/BLOCKED.md`에 적고 끝낸다. 리팩터링은 하지 않는다(리뷰 단계 몫).
- 버그를 고치는 과제면 먼저 `mattpocock-skills:diagnosing-bugs` 스킬로 재현하고 원인을 찾는다.
- 스킬 이름은 앞 이름까지 다 쓴다. 앞 이름 없는 이름은 다른 스킬로 갈 수 있다.
- `{job}/check.md`가 있으면 직전 검사가 실패한 것이다. 그 출력을 보고 고친다.
- 테스트: `python3 {office}/pipeline/engine.py test {name}`(테스트 명령 `{test}`). 돌려서 통과를 확인한다.
- 변경을 `{job}/repo/`에서 커밋한다. 커밋하지 않은 변경을 남기지 않는다.
- 끝나면 `{job}/implement.md`에 커밋 SHA, 실행한 명령과 결과, 확인하지 않은 범위를 적는다.
- 그다음 `{job}/pr-body.md`에 PR 본문을 쓰고 끝낸다. 검사 단계가 통과하면 엔진이 이 파일로 PR을 연다.
  - 첫 줄은 `# <PR 제목>`이다. 제목은 그 저장소 커밋 메시지의 형식을 따른다. 엔진은 이 줄을 제목으로 쓰고 본문에서 뺀다.
  - 그 아래는 `{job}/repo/.github/pull_request_template.md`의 칸을 그대로 채운다. 그 파일이 없으면 repo-baseline 클론의 `{baseline}/.github/pull_request_template.md`를 쓴다.
  - `완료 기준:`은 `{job}/INTENT.md`와 `{job}/design.md`에서 옮긴다. 되돌리기 판정과 되돌리는 법은 `design.md`의 판단을 옮긴다. `되돌리기:` 줄은 `되돌리기: 쉬움` 또는 `되돌리기: 어려움`만 쓰고, 근거는 다음 줄에 쓴다. `민감한 파일:` 줄은 건드리지 않았으면 `민감한 파일: 없음`만 쓴다.
  - 「그림」 줄은 모듈 경계나 흐름, 파일 구성이 바뀔 때만 Mermaid·의사 코드·파일 트리 중 하나로 채운다. 작은 변경이면 그 줄을 지운다. `그림:` 다음 줄부터 코드 블록으로 쓴다. Mermaid는 ` ```mermaid `로 연다(GitHub이 그림으로 그린다). 의사 코드와 파일 트리는 ` ``` `로 연다. 그림 파일(HTML·SVG·PNG)은 만들지 않는다. PR 본문에 올릴 수 없다.
  - Evidence에는 실행한 명령과 결과, 확인하지 않은 범위를 적는다. Review 칸은 비워 둔다. 리뷰 단계가 채운다.
  - PR은 열리는 즉시 공개될 수 있다. 우선순위·일정·내부 판단, 취약점 설명, 비밀값을 적지 않는다.
- push·PR 열기·병합·배포는 하지 않는다. push와 PR 열기는 엔진이 한다.
- 막히면 지어내지 말고 `{job}/BLOCKED.md`에 이유를 적고 끝낸다.
- 끝낼 때는 `{job}/implement.md`나 `{job}/BLOCKED.md` 중 하나를 반드시 쓴다. 앞 라운드에서 이미 끝난 일이어도 이번 결과를 새로 쓴다. 둘 다 없으면 엔진은 실패로 센다.
