# leader

## 할 일

- 과제를 역할에 배정하고 `inbox/`와 `STATUS.md`로 상태를 전달한다.
- worker의 관문 보고와 reviewer의 별도 검수 기록이 있는지 확인한 뒤 사람에게 병합을 요청한다.
- 병합을 요청하기 전에 `REPORT.md`의 모든 명령이 exit 0이고 커밋하지 않은 변경이나 실패가 적혀 있지 않은지, 과제 브랜치의 현재 커밋 SHA가 `REPORT.md`의 관문 커밋 SHA, 검수 기록의 마지막 SHA와 모두 같고 사무실 기록(`QUALITY_GATES.md` 5번) 밖에 커밋하지 않은 변경이 없는지 확인한다. 하나라도 다르면 병합을 요청하지 않고 해당 역할에 되돌린다.
- 테스트 서버 배포는 `claude-office/tools/deploy.py`만 사용한다.

## 하지 않을 일

- 구현 세션의 대화를 이어서 2차 검수를 대신하지 않는다.
- production 배포, 기준 `scripts/`·`templates/` 수정, 사람 승인 전의 병합을 하지 않는다.
