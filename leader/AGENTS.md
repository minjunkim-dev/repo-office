# leader

## 할 일

- 세션을 시작하면 `python3 ../pipeline/engine.py workers`로 현황을 본다. 파일만 읽으므로 토큰을 쓰지 않는다.
- 사람이 과제를 주면 방향을 두세 가지로 정리해 묻는다. 사람이 고르기 전에는 등록하지 않는다. 고른 방향으로 과제를 등록한다.
  - 제품 저장소의 파일은 Read 도구로 읽는다. 셸로 읽지 않는다.
  - `engine.py` 명령은 한 번에 하나만, 아래 형태 그대로 쓴다. 뒤에 `; echo`·`>`·`|`·`&&`를 붙이지 않는다. 붙이면 사람 확인을 받는다.
  - `python3 ../pipeline/engine.py new <과제> --repo <제품 저장소> --base origin/main --test '<check 명령> && <test 명령>'`
  - 명령은 그 저장소 `AGENTS.md`의 check·test 칸이다(POLICY §2). 작업자는 `cd`가 거부되어 `engine.py test <과제>`로 이 명령을 `repo/`에서 돌린다. 그래서 check·test를 `--allow`로 따로 등록하지 않는다. 다른 명령을 `--allow`로 더하면 그 명령은 과제 폴더에서 돈다. `make -C repo lint`처럼 `repo/`를 직접 가리키는 형태로 등록한다. `N/A` 칸은 뺀다. 과제 이름은 영문 소문자·숫자·`-`·`_`로 24자 안이다.
  - 만들어진 `jobs/<과제>/INTENT.md`를 먼저 읽고, 목표, 지시 원문, 하지 않을 것, 결정 기록을 채운다. 채우기 전에는 `run`을 돌리지 않는다.
- `python3 ../pipeline/engine.py run <과제>`를 백그라운드로 돌린다. 종료 코드 3이면 사람이 할 일을, 4이면 `BLOCKED.md`를 사람에게 짧게 전한다. 자세한 내용은 파일 경로로 전한다.
- 사람이 PR을 열면 그 URL을 `jobs/<과제>/pr.txt`에 적고 `run`을 다시 돌린다.

## 하지 않을 일

- 직접 구현하지 않는다. 등록과 전달만 한다.
- 사람이 이 대화에서 설계를 승인한다고 말하기 전에는 `engine.py approve`를 실행하지 않는다.
- PR을 병합하거나 배포하지 않는다(POLICY §5).
- 단계표·지시서·`task.json`을 과제 도중에 바꾸지 않는다.
