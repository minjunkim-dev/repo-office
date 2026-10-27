# 팀 공통 규칙

이 폴더부터 연 세션은 역할 폴더의 `AGENTS.md`와 이 파일을 함께 읽는다. 제품 저장소의 규칙과 여기가 다르면 제품 저장소의 검사·정책이 우선한다.

- 세션 하나는 역할 폴더 하나에서 연다.
- 세션 사이 전달은 `leader/inbox/`와 과제 폴더의 파일로 한다.
- 방향, 설계, 실서비스 배포는 사람이 승인한다.
- 작은 수정은 사무실을 열지 않고 제품 저장소에서 바로 구현한다.
- 제품 셋업과 `tools/apply_baseline.py` 실행은 정본인 `minjunkim-dev/repo-baseline`에서 한다.
- production 배포 자격 증명은 세션 환경에 두지 않는다. `tools/deploy.py`는 대상 선택을 staging으로 막는 계획 도구이고 실제 배포는 하지 않는다.
- 같은 지적이 두 번 나오면 `MEMORY.md`의 한 줄을 `HANDBOOK.md` 정식 규칙으로 올린다. 그 규칙이 `scripts/`나 `templates/`를 바꿔야 하면 `minjunkim-dev/repo-baseline`에서 고친 뒤 제품에 다시 적용한다.
