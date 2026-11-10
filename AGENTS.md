# claude-office

PR을 열기 전의 작업을 단계로 나눠 돌리는 실험 프로필이다. PR을 연 뒤의 리뷰·병합·회고는 repo-baseline(`../POLICY.md`, `../prompts/`)을 그대로 따른다. 여기서 그 규칙을 다시 적거나 바꾸지 않는다.

## 구조

| 경로 | 무엇 |
| --- | --- |
| `leader/` | 유일한 상주 세션. 사람과 말하고 과제를 등록한다 |
| `pipeline/stages.json` | 단계표. 단계마다 담당(claude·script·human), 지시서, 결과 파일, 허용 도구 |
| `pipeline/prompts/` | 단계별 지시서 |
| `pipeline/engine.py` | 단계표대로 다음 단계를 띄운다. 작업자는 `hc` 터미널의 대화형 Claude Code다 |
| `HANDOFF.md` | 이어받는 사람이 먼저 읽는 상태·함정·남은 이슈 |
| `jobs/<과제>/` | 과제마다 `INTENT.md`, 단계 결과 파일, 작업 브랜치의 worktree `repo/`. git에 넣지 않는다 |

## 규칙

- 상주 세션은 leader 하나다. 작업자는 단계마다 띄우고, 결과 파일을 쓰면 엔진이 닫는다.
- 한 과제는 세션 키마다 세션 ID 하나를 쓴다. 설계와 구현은 같은 세션을 `--resume`으로 이어 캐시를 쓴다. 리뷰는 다른 세션이다(POLICY §3).
- 단계마다 허용 도구만 준다(`--permission-mode dontAsk`). 사용자 전역 설정의 허용 규칙은 읽지 않는다(`--setting-sources project`). 이 설정은 office 체크아웃 기준이라 제품 저장소의 `.claude/settings.json`도 읽지 않는다.
- 거부 목록은 Edit·Write 도구로 이 폴더의 규칙·단계표, 기준 파일(`tools/`, `prompts/` 등), `task.json`·`state.json`·`pr.txt`를 고치는 것과 `.env*` 읽기, `gh pr merge`·`gh api` 쓰기를 막는다. 샌드박스는 아니다. 허용한 테스트 명령과 검사 단계는 사용자 권한으로 코드를 실행하므로 그 코드는 무엇이든 고칠 수 있다(POLICY §5와 같다).
- 설계 승인은 사람이 한다. 승인은 `design.md`의 sha256에 묶인다. 승인 뒤 한 글자라도 바뀌면 다시 승인받는다.
- 같은 단계가 두 번 연속 실패하거나 작업자가 `BLOCKED.md`를 쓰면 멈춘다. 엔진은 리뷰 단계를 한 번만 돌리고 끝낸다. 남은 지적으로 2·3라운드를 할지는 소유자가 정한다(`prompts/review.md`의 최대 3라운드). 엔진은 그 라운드를 돌리지 않는다.
- PR을 여는 것과 병합은 사람이 한다. 엔진과 작업자는 병합·배포·`main` push를 하지 않는다. push는 리뷰 작업자가 리뷰에서 고친 커밋을 PR 브랜치에 올릴 때만 한다(`git -C <job>/repo push origin HEAD`). production 자격 증명은 세션 환경에 두지 않는다.
- 결정적 검사(테스트, 커밋하지 않은 변경)는 스크립트 단계가 한다. 모델은 판단이 필요한 단계에만 쓴다.
