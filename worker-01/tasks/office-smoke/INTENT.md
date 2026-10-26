# office-smoke

## 목표

기준 검사와 사무실 관문이 한 저장소에서 함께 통과하는지 확인한다.

## 지시 원문

repo-baseline 복사본에 Claude Code 팀 폴더를 얹고, 배포 도구가 production을 거절하는지, 복사 manifest에 사무실이 없는지, 기준 테스트가 통과하는지 확인한다.

## 하지 않을 것

- `minjunkim-dev/repo-baseline` 원본을 수정하지 않는다.
- smallnext, BGLike, mobile-runtime에 적용하지 않는다.
- production 배포나 서버 설정 변경을 하지 않는다.

## 결정 기록

- 사무실은 `claude-office/`에 두고 제품 복사 manifest 밖에 둔다.
- 테스트 명령은 원본 CI와 같은 unittest discover를 사용한다.
- 이 과제에서 린트와 화면은 비해당이다.
