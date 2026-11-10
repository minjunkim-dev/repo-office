# 이어받기

이 문서는 다른 사람·계정·에이전트가 claude-office 작업을 바로 이어받기 위한 기록이다. 2026-11-10에 PR #10과 함께 썼다. 남은 일은 GitHub 이슈에 있고, 이 문서는 그 배경과 함정을 적는다.

## 1. 지금 상태

- PR #10(브랜치 `claude/repo-baseline-update-test-763eb5`)은 열려 있고 병합 전이다.
  - 기준 파일을 원본 `minjunkim-dev/repo-baseline`의 `c3f6d56`과 같게 맞췄다.
  - `claude-office/`를 단계 파이프라인으로 다시 지었다.
- `claude-office/` 밖에서 원본과 다른 곳은 다섯 곳뿐이다. 목록은 루트 README 머리말에 있다.
- 다섯 곳이 그대로인지는 다음 명령으로 확인한다. `upstream/main`은 원본의 `main`을 받아 둔 ref다.

  ```bash
  git fetch https://github.com/minjunkim-dev/repo-baseline main:refs/remotes/upstream/main
  git diff upstream/main --stat -- . ':!claude-office'
  ```

- 검사는 루트 `AGENTS.md`의 test 명령이다. 마지막 실행은 exit 0이었다.

## 2. 남은 일(이슈)

| 이슈 | 무엇 | 먼저 할 것 |
| --- | --- | --- |
| #11 | PR #10 새 세션 리뷰·소유자 병합 | 가장 먼저 한다 |
| #12 | 리뷰 단계를 실제 PR로 검증 | #11 다음 |
| #13 | leader 세션으로 실제 제품 과제 하나 끝까지 | #12 다음 |
| #14 | 여러 과제 동시 실행, 3분 넘는 단계 | 아무 때나 |
| #15 | OS 샌드박스 검토, git 허용 규칙의 근본 대책 | 실제 제품에 쓰기 전에 |
| #16 | 영상에서 뺀 기능(알림, 다른 모델, 동시 상한, 시작 훅) | 필요할 때만 |

## 3. 왜 이렇게 지었나

참고한 두 영상(Solostack, Claude Code 팀 운영)의 줄거리는 다음과 같다.

- 1편(2026-10-24): 역할마다 폴더와 세션을 둔다. `INTENT.md`를 쓰고 설계를 승인받은 뒤 구현한다. 다섯 관문을 거치고 MEMORY→HANDBOOK으로 규칙을 올린다. 위험한 작업은 도구 권한으로 막는다.
- 2편(2026-11-05): 상주 세션은 대장 하나만 둔다. 작업자는 단계표(`stages.json`)대로 그때그때 띄운다. 과제마다 세션 ID 하나로 앞 단계를 이어 써서 캐시를 쓴다. 단계마다 허용 명령을 따로 주고, 승인은 설계 파일 내용에 묶는다. 반복 실패하면 멈추고, `workers` 명령으로 현황을 본다.

원본 repo-baseline은 PR이 `main`에 들어가는 조건을 정한다(완료 증거, 다른 컨텍스트 리뷰, Door, 병합). 영상은 PR 전의 작업 조율을 다룬다. 층이 달라 서로 보완한다. 부딪히는 곳은 모두 원본 쪽으로 정했다.

| 영상 | 원본과의 관계 | 정한 것 |
| --- | --- | --- |
| 다섯 관문, MEMORY→HANDBOOK, reviewer 규칙 | POLICY §2·§6, `prompts/review.md`와 겹침 | 지우고 원본을 따른다 |
| 자체 검수가 구현 세션을 이어 씀 | §3: 물려받은 대화는 다른 컨텍스트가 아님 | 리뷰는 다른 세션 키 |
| 리더가 마지막 판정 | §5: 소유자가 병합 | 엔진·작업자는 병합·push to main 안 함 |
| `CLAUDE.md` | 원본은 `AGENTS.md` 하나 | `AGENTS.md`만 |
| `claude -p`(일회성) | 사용자 전역 규칙이 `-p`를 금지하고 `hc`를 권함 | `hc run --bg`로 대화형 claude |
| 리뷰 반복 3회 초과 시 멈춤 | §3 최대 3라운드 | 엔진은 리뷰를 한 번 돌리고, 추가 라운드는 소유자가 정함 |

## 4. 실제로 돌려서 알게 된 함정

지침·코드에 이미 반영했다. 다시 바꿀 때 같은 실수를 하지 않도록 적는다.

- 첫 지시를 `hc run ... claude "<지시>"`처럼 실행 인자로 주면 사라진다. 신뢰 창이 뜨거나 `--resume`일 때 그랬다. 그래서 엔진은 띄우고, `hc wait`로 준비를 기다린 뒤 `hc send --wait`로 짧은 메시지를 보낸다. 메시지는 지시서 파일 경로 하나다.
- 새 git worktree를 작업자 cwd로 쓰면 폴더 신뢰 창이 뜬다. 엔진은 화면에서 신뢰 창을 알아채면 키를 누르지 않고 `BLOCKED.md`를 쓴다. 그래서 작업자는 이미 신뢰된 office 체크아웃 안의 `jobs/<과제>/`에서 띄우고, 코드는 `repo/` 하위 폴더에서 고친다.
- 그 cwd는 office 체크아웃 안이다. 그래서 git 쓰기 명령은 `git -C <job>/repo ...` 절대 경로 형태만 허용한다. 일반 `git commit`을 허용하면 office 브랜치에 커밋된다.
- 작업자가 `&&`·`|`로 명령을 이어 붙이면, 한 부분이라도 허용 밖일 때 통째로 거부된다. Haiku는 "하나씩"이라는 지시를 자주 어겼다. Sonnet은 지켰다.
- 작업자가 이전 라운드를 기억하고 "할 일 없음"이라며 결과 파일 없이 끝낸 적이 있다. 지시서에 "결과 파일이나 `BLOCKED.md` 중 하나를 반드시 쓴다"를 넣었다.
- 검사 단계가 테스트가 만든 `__pycache__`를 미커밋 변경으로 셌다. 그래서 git 상태는 테스트 전에 본다.
- `--setting-sources project`를 주면 사용자 전역 허용 규칙이 새지 않는다. 대신 제품 저장소의 `.claude/settings.json`도 읽지 않는다.
- 경로 제한 없는 Write·Edit는 위험하다. 작업자가 `.git/hooks`를 심으면 허용된 `git commit`이 그것을 실행한다. 지금은 과제 폴더의 md와 `repo/**`로만 좁혔다.
- 거부 목록은 샌드박스가 아니다. 허용한 테스트 명령은 임의 코드를 실행한다. git 와일드카드 규칙은 부분 문자열 거부로 막고 있어 근본 대책이 아니다(#15).

## 5. 다시 재현하는 법

토큰을 적게 쓰려면 단계표를 복사해 각 claude 단계에 `"model": "sonnet"`을 넣고 `OFFICE_STAGES`로 지정한다. Haiku는 위 함정 때문에 Bash가 자주 막힌다.

```bash
# 장난감 제품 저장소
mkdir -p /tmp/toy && cd /tmp/toy && git init -q -b main
printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n' > test_calc.py
printf '__pycache__/\n' > .gitignore && git add -A && git commit -qm init

# office 체크아웃 루트에서(이 폴더는 Claude Code에서 한 번 신뢰해 둔다)
cd claude-office
python3 pipeline/engine.py new toy-subtract --repo /tmp/toy --test 'python3 -m unittest' --allow 'Bash(python3 -m unittest*)'
# jobs/toy-subtract/INTENT.md에 목표를 쓴다. 예: calc.py에 subtract(a, b)와 테스트를 더한다
OFFICE_STAGES=/tmp/stages-sonnet.json python3 pipeline/engine.py run toy-subtract   # exit 3: 설계 승인 대기
python3 pipeline/engine.py approve toy-subtract                                       # 사람이 design.md를 읽은 뒤
OFFICE_STAGES=/tmp/stages-sonnet.json python3 pipeline/engine.py run toy-subtract   # exit 3: pr.txt 대기
python3 pipeline/engine.py workers
```

권한 시험은 엔진의 `claude_argv()`로 단계와 같은 인자를 만든다. 그다음 `run_worker()`로 작업자를 띄우고 "소유자의 권한 시험"이라고 밝혀서 시킨다. 밝히지 않으면 모델이 규칙을 보고 아예 시도하지 않는다. 그러면 도구가 막는지 확인할 수 없다.

## 6. 이어받을 때 주의

- 정본은 원본 `minjunkim-dev/repo-baseline`이다. 소비 레포 셋업과 `profiles/hash-copy/apply_baseline.py`는 원본에서 실행한다.
- 원본을 다시 맞출 때는 원본 `main`으로 `claude-office/` 밖을 통째로 바꾼다. 그다음 README 머리말의 다섯 곳만 다시 더한다.
- `gh`는 이 체크아웃에서 원본 저장소를 기본으로 잡을 수 있다. PR·이슈 명령에는 `--repo minjunkim-dev/repo-baseline-office`를 붙인다.
- 원본 `tools/review_preflight.py`에 자동 보안 리뷰가 지적한 셸 인용 문제가 하나 있다. 이 저장소에서는 원본과 같게 두므로 원본에서 고친다. 보안 지적이므로 공개 이슈가 아니라 비공개 경로로 알린다(POLICY §7).
