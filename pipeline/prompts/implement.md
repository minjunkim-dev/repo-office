# 구현 단계

이 단계 하나만 한다. 셸 명령은 허용된 것만, 한 번에 하나씩 쓴다. `&&`·`;`·`|`로 잇지 않는다. 이으면 통째로 거부된다. `cd`는 거부된다. git 읽기는 `git -C repo ...`로 쓴다. 테스트는 `python3 {office}/pipeline/engine.py test {name}`로 돌린다. 엔진이 `repo/`에서 테스트 명령을 돌리고 그 종료 코드로 끝난다. git add·commit·push는 `git -C {job}/repo ...`로만 쓴다. commit은 `git -C {job}/repo commit -m "<메시지>"` 형태만 된다(`-am`, `-F`는 거부된다). 사람이 승인한 `{job}/design.md` 그대로 구현한다.

- 작업할 곳: `{job}/repo/` (브랜치 `{branch}`).
- 설계에 없는 변경을 더하지 않는다. 설계를 바꿔야 하면 구현하지 말고 `{job}/BLOCKED.md`에 적고 끝낸다.
- `mattpocock-skills:tdd` 스킬을 불러 빨강→초록으로 구현한다. 그 스킬이 seam을 사용자와 확인하라고 하면, 승인된 `{job}/design.md`의 「테스트할 seam」 목록으로 대신한다. 목록 밖 seam이 필요하면 구현하지 말고 `{job}/BLOCKED.md`에 적고 끝낸다. 리팩터링은 하지 않는다(리뷰 단계 몫).
- 버그를 고치는 과제면 먼저 `mattpocock-skills:diagnosing-bugs` 스킬로 재현하고 원인을 찾는다.
- 스킬 이름은 앞 이름까지 다 쓴다. 앞 이름 없는 이름은 다른 스킬로 갈 수 있다.
- `{job}/check.md`가 있으면 직전 검사가 실패한 것이다. 그 출력을 보고 고친다.
- 테스트: `python3 {office}/pipeline/engine.py test {name}`(테스트 명령 `{test}`). 돌려서 통과를 확인한다.
- 변경을 `{job}/repo/`에서 커밋한다. 커밋하지 않은 변경을 남기지 않는다.
- 끝나면 `{job}/implement.md`에 커밋 SHA, 실행한 명령과 결과, 확인하지 않은 범위를 적고 끝낸다.
- push·PR 열기·병합·배포는 하지 않는다.
- 막히면 지어내지 말고 `{job}/BLOCKED.md`에 이유를 적고 끝낸다.
- 끝낼 때는 `{job}/implement.md`나 `{job}/BLOCKED.md` 중 하나를 반드시 쓴다. 앞 라운드에서 이미 끝난 일이어도 이번 결과를 새로 쓴다. 둘 다 없으면 엔진은 실패로 센다.
