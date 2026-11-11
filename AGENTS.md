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
- 거부 목록은 Edit·Write 도구로 이 폴더의 규칙·단계표, 기준 파일(`tools/`, `prompts/` 등), `task.json`·`state.json`·`pr.txt`를 고치는 것과 `.env*` 읽기, `gh pr merge`·`gh api` 쓰기를 막는다. 거부 목록만으로는 막지 못한다. 허용한 테스트 명령은 작업자가 쓴 코드를 실행하기 때문이다.
- 작업자의 Bash와 그것이 띄운 프로세스(테스트, git)는 Claude Code sandbox 안에서 돈다(단계표 `sandbox`, macOS Seatbelt). 쓰기는 과제 폴더와 제품 저장소의 git 데이터(커밋에 필요한 objects·refs와 이 worktree의 index)만 된다. office 체크아웃은 `jobs/` 말고 모두 쓰기를 막는다. 과제 폴더 안에서도 엔진의 제어 파일(`task.json`·`state.json`·`pr.txt`·`pr.diff`), `repo/.git` 파일, 제품 저장소의 `config`·`hooks`·`info`와 worktree 연결 파일, 아직 없는 지시·설정 파일(`CLAUDE.md`·`AGENTS.md`·`.claude`·`.mcp.json`·`.envrc`·`.git`)은 쓰기를 막는다. sandbox 밖에서 도는 명령의 git이 그 설정과 hook을 따르기 때문이다. 파일을 막는 것만으로는 다 막지 못한다(과제 코드가 커밋한 하위 저장소의 설정). 그래서 엔진이 sandbox 밖에서 돌리는 git(검사 단계, `engine.py preflight`)에는 환경 변수로 `core.fsmonitor=false`, `diff.ignoreSubmodules=all`, `core.hooksPath=/dev/null`, `fetch.recurseSubmodules=false`, `submodule.recurse=false`, `protocol.ext.allow=never`를 강제한다. 리뷰의 `push`는 작업자가 직접 돌리므로 이 강제가 없다. push는 기본으로 하위 저장소로 내려가지 않는다. 홈 폴더 읽기는 위 읽기 설정이 막는다. sandbox 밖으로 다시 시도하는 길은 닫는다(`allowUnsandboxedCommands: false`). sandbox를 띄우지 못하면 작업자가 시작하지 않는다(`failIfUnavailable`). 허용 목록은 그대로 듣는다(`autoAllowBashIfSandboxed: false`). 네트워크는 `github.com`만 된다. 키체인이 필요한 `gh` 읽기·`gh pr edit`, `engine.py preflight`, 리뷰의 `push`는 sandbox 밖에서 돈다(`excludedCommands`). 이 명령들은 과제 코드를 직접 실행하지 않지만 사용자의 `gh`·git 권한을 그대로 쓴다. `gh pr edit`은 허용 목록이 받는 어떤 PR이든 고친다. 남는 구멍: 다른 과제 폴더는 쓰기를 막는다. 단, 단계가 도는 동안 새로 생긴 과제 폴더와 worktree 연결 폴더는 막지 못한다(목록은 작업자를 띄울 때 만든다). sandbox 안의 코드도 `github.com`에는 닿으므로, 상속받은 환경 변수나 저장소 내용을 다른 GitHub 저장소로 보낼 수 있다. 세션 환경에 비밀값을 두지 않는다. 근거: [sandboxing](https://code.claude.com/docs/en/sandboxing)(2026-11-11 확인).
- 작업자의 읽기는 과제 폴더와 이 체크아웃으로 좁힌다(`--add-dir`, `permissions.blockReadsOutsideWorkingDirectories`, Claude Code 2.1.257 이상). 이보다 낮은 판은 이 설정을 말없이 무시하므로, 엔진은 작업자를 띄우기 전에 `claude --version`을 보고 낮거나 읽지 못하면 `BLOCKED.md`를 쓴다. 이 설정이 없으면 `Read`와 `cat`이 홈의 자격 증명 파일을 읽는다. 리뷰 단계의 `git fetch`는 `origin` 한 형태만 허용한다. `git fetch*`는 `--upload-pa=<명령>`(git은 긴 옵션의 줄임말을 받는다)으로 명령을 실행하고 refspec으로 로컬 ref를 쓴다. `git diff`는 `--no-index` 없이도 저장소 밖 경로의 내용을 출력하므로(`git diff /dev/null <밖의 파일>`), 인자에 절대 경로·`~`·`..` 경로·따옴표·`$`가 든 diff를 거부한다. 이 거부 목록은 경로 문법을 다 막지 못한다(`HANDOFF.md` §4). commit은 `commit -m ...` 형태만 허용한다. `commit -F <파일>`은 작업 폴더 밖 파일을 커밋 메시지로 읽고, 위 설정은 이 읽기를 막지 않는다. `--pathspec-from-file`과 `git difftool`도 거부한다. 근거: [permissions](https://code.claude.com/docs/en/permissions), [settings-reference](https://code.claude.com/docs/en/settings-reference#permissions-blockreadsoutsideworkingdirectories)(2026-11-10 확인).
- 검사 단계(`check`)는 엔진이 테스트를 macOS `sandbox-exec`로 감싸 돌린다(`check_profile`). 작업자 sandbox와 같게, 쓰기는 과제 폴더(제어 파일·`repo/.git`·지시 파일 제외)와 `/dev`만 되고, 임시 폴더는 `<과제>/tmp`로 돌리고, 네트워크는 localhost와 과제 폴더 안의 unix socket만 되고(`/var/run/docker.sock` 같은 밖의 socket은 sandbox 밖에 프로세스를 띄울 수 있다), 홈 폴더는 과제 폴더·이 체크아웃·제품 저장소의 git 데이터 말고 읽지 못한다. `sandbox-exec`가 없으면(macOS가 아니면) `BLOCKED.md`를 쓴다. `sandbox-exec`는 Apple이 deprecated로 표시했지만 macOS 27.2에 들어 있고 동작한다(2026-11-11 확인). 작업자의 `engine.py test`는 이미 Claude Code sandbox 안이라 감싸지 않는다. 두 sandbox는 실수나 사고로 과제 폴더 밖을 망가뜨리는 것을 막는다. 일부러 뚫으려는 코드는 막는 대상이 아니다. 시험하지 않은 길: 상위 폴더 이름 바꾸기로 쓰기 금지를 피하는 것(작업자 sandbox), `open`·AppleEvent로 sandbox 밖 프로세스를 띄우는 것(검사 단계). 그것까지 막으려면 별도 OS 사용자나 VM이 필요하다.
- 설계 승인은 사람이 한다. 승인은 `design.md`의 sha256에 묶인다. 승인 뒤 한 글자라도 바뀌면 다시 승인받는다.
- 같은 단계가 두 번 연속 실패하거나 작업자가 `BLOCKED.md`를 쓰면 멈춘다. 엔진은 리뷰 단계를 한 번만 돌리고 끝낸다. 남은 지적으로 2·3라운드를 할지는 소유자가 정한다(`prompts/review.md`의 최대 3라운드). 엔진은 그 라운드를 돌리지 않는다.
- PR을 여는 것과 병합은 사람이 한다. 엔진과 작업자는 병합·배포·`main` push를 하지 않는다. push는 리뷰 작업자가 리뷰에서 고친 커밋을 PR 브랜치에 올릴 때만 한다(`git -C <job>/repo push origin HEAD`). production 자격 증명은 세션 환경에 두지 않는다.
- 결정적 검사(테스트, 커밋하지 않은 변경)는 스크립트 단계가 한다. 모델은 판단이 필요한 단계에만 쓴다.
