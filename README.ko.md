# claude-session-manager (`csm`)

**[English README](./README.md)**

여러 개의 Claude Code 세션을 한 곳에서 추적하고, 우선순위를 부여하고, 그 창으로 바로 이동하고, 닫힌 세션을 재개한다.

Claude Code 창이 3개 프로젝트에 걸쳐 5개 떠 있을 때, `csm` 명령 하나로 전부 보고, 원하는 터미널로 점프하고, 종료된 세션은 새 창에서 재개할 수 있다.

```
● 3f9a2c  [high]  in_progress  refactor auth middleware        ~/src/api       2m ago
  ⤷ can you split the token verifier into its own module?
  ⚙ Editing: src/auth/verify.ts
○ 7b1e40  [med]   waiting      add i18n sync skill             ~/skills        18m ago
  ⤷ wire the CLI to detect next-intl automatically
  ⚙ Running: pytest tests/
⚠ 1 stale sessions — run 'csm review-stale'
```

## 주요 기능

- **실시간 세션 레지스트리** — 모든 Claude Code 세션이 `SessionStart` / `UserPromptSubmit` 훅으로 자동 등록된다. 수동 기록 불필요.
- **한눈에 진행 상황** — 마지막 사용자 프롬프트와 현재 tool 활동(`Editing: foo.ts`, `Running: pytest`)이 각 행 아래 렌더링된다.
- **3단계 상태 도트** — `●` claude 프로세스 살아 있음, `◉` 창 열려 있고 프로세스 종료, `○` 창 닫힘.
- **우선순위 + 상태 분류** — 세션에 `high`/`medium`/`low` + `in_progress`/`blocked`/`waiting`/`done` 태그.
- **Stale 감지** — 설정된 임계치(기본 4시간) 이상 활동 없는 세션을 표시. `csm review-stale`이 keep/done/archive를 하나씩 묻는다.
- **크로스 터미널 focus & resume** — `csm focus <id>`는 순서대로 시도: tmux IPC → 네이티브 IPC(iTerm2 / Terminal.app AppleScript, WezTerm `wezterm cli`, Kitty `kitty @`) → 제목 마커 매칭(macOS System Events, X11 `wmctrl` / `xdotool`, Wayland `swaymsg`). `csm resume <id>`는 가용한 터미널에서 새 창을 연다.
- **Statusline 통합** — Claude Code 하단 상태바에 pending/stale 요약 표시.
- **슬래시 명령** — `/tasks`, `/task-register`, `/task-focus`, `/task-done`, `/done`, `/task-priority`, `/task-note`, `/task-status`가 어떤 Claude Code 세션 안에서든 동작한다.

## 요구 사항

- macOS, Linux (X11 또는 Wayland); tmux는 선택
- Python 3.9+ (`csm list` 컬러 출력에 `rich` 선택, `csm watch`는 stdlib curses)
- Claude Code

## 설치

```bash
git clone <this-repo>
cd claude-session-manager
bash install.sh
```

인스톨러는 idempotent하게 다음을 수행한다:

1. `~/.local/bin/csm` → `scripts/csm.py` 심볼릭 링크
2. `~/.claude/skills/claude-session-manager` → 이 디렉토리 심볼릭 링크
3. `commands/*.md` → `~/.claude/commands/` 심볼릭 링크 (슬래시 명령)
4. `~/.claude/claude-tasks/` 레지스트리 디렉토리 생성
5. `SessionStart`/`UserPromptSubmit` 훅을 `~/.claude/settings.json`에 병합 (중복 방지, 다른 엔트리 보존)
6. statusLine이 비어 있을 때만 `csm statusline` 설치
7. 마지막에 `csm list`로 스모크 테스트

`~/.local/bin`이 PATH에 있어야 한다:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 빠른 시작

Claude Code 창 두어 개 열어두고 아무 터미널에서:

```bash
csm list                   # 모든 세션 보기
csm set 3f9a2c --priority high --title "refactor auth"
csm focus 3f9a2c           # 해당 창으로 이동
csm resume 7b1e40          # 닫힌 세션을 새 창에서 재개
csm review-stale           # stale 세션 대화형 정리
```

Short-id 프리픽스 매칭은 6자 이상이면 OK. 애매하면 후보를 출력하고 아무것도 변경하지 않고 종료한다.

## 명령 참조

```
csm list                            멀티라인: 헤드라인 + ⤷ 프롬프트 + ⚙ 힌트
csm list --compact                  한 줄씩
csm list --all                      archived 포함
csm list --stale                    stale만
csm list --json                     JSON 출력

csm current                         현재 세션 id 출력 ($CLAUDE_SESSION_ID 또는 cwd 추론)

csm set <id> [--title ...] [--priority high|medium|low]
            [--status in_progress|blocked|waiting|done]
            [--note ...] [--tags a,b]
csm done    <id>                    done 표시
csm archive <id>                    archive (기본 목록에서 숨김)

csm focus   <id>                    창 앞으로
csm resume  <id>                    새 창에서 `claude --resume <id>` 실행

csm watch                           curses 대화형 TUI (실시간 갱신)
csm watch --pin                     전용 창에 watch 고정

csm review-stale                    keep/done/archive/skip 순회
csm gc                              7일 이상 archived 레코드 삭제
csm statusline                      pending/stale 요약 한 줄
csm scan                            ~/.claude/projects/ 트랜스크립트 재스캔

csm --version
```

### Id 해석 exit code

| Code | 의미 |
|------|---------|
| 1 | 미발견 |
| 2 | 프리픽스가 6자 미만 |
| 3 | 프리픽스 중복 (stderr에 후보 출력) |
| 6 | macOS 전용 서브명령 |

## 슬래시 명령

자동 설치되며 Claude Code 세션 안에서 사용:

| 명령 | 용도 |
|---------|---------|
| `/tasks` | 추적 중인 세션 목록 |
| `/task-register` | 현재 세션에 제목/우선순위 등록 |
| `/task-focus <id>` | 다른 세션 창으로 이동 |
| `/task-priority <level>` | 현재 세션 우선순위 설정 |
| `/task-status <status>` | 현재 세션 상태 설정 |
| `/task-note <text>` | 현재 세션에 메모 |
| `/task-done`, `/done` | 현재 세션 done 표시 |

`$CLAUDE_SESSION_ID`가 비어 있으면 자동으로 `csm current`로 폴백하므로 구 버전 Claude Code에서도 동작한다.

## 진행 상황 캡처

`csm`은 각 세션 트랜스크립트의 마지막 50 JSONL 줄에서 세 필드를 추출한다 — AI 호출 없음, 네트워크 없음:

- **`last_user_prompt`** — 최근 사용자 메시지 (`UserPromptSubmit` 훅이 즉시 갱신; 스캐너는 트랜스크립트 mtime이 더 최신일 때만 덮어씀).
- **`last_assistant_summary`** — 최근 어시스턴트 응답.
- **`current_task_hint`** — 마지막 `tool_use`에서 파생:
  - `Bash` → `Running: <command>`
  - `Edit`/`Write`/`MultiEdit` → `Editing: <path>`
  - `Read` → `Reading: <path>`
  - `Grep`/`Glob` → `Searching: <pattern>`

세 필드 모두 100 코드포인트로 잘리고 말미에 `…` 추가.

## TUI (`csm watch`)

curses 기반 대화형 뷰. 키 바인딩:

- **↑↓ / k / j** 이동, **PgUp/PgDn / Home/End** 페이지 이동
- **Enter** 선택한 세션 창으로 focus (창 닫혀 있고 in_progress면 자동 resume)
- **r** 새 창에서 resume, **n** 메모 편집
- **p** 우선순위 순환, **s** 상태 순환
- **d** done, **a** archive
- **/** 검색 필터, **?** 도움말, **q/Esc** 종료

선택된 행의 프로젝트 경로가 컬럼 폭을 초과하면 마키 스크롤로 흐른다. 한글/이모지 너비를 정확히 측정해 정렬이 깨지지 않는다.

## 설정

선택 사항: `~/.claude/claude-tasks.config.json`

```json
{ "stale_threshold_seconds": 3600 }
```

기본값 `14400` (4시간). 유효하지 않은 값은 기본값으로 폴백하고 `~/.claude/claude-tasks/.scanner-errors.log`에 기록된다. 환경변수 `CST_STALE_THRESHOLD_SECONDS`가 파일보다 우선 (테스트 용도).

## 동작 원리

```
Claude Code 세션
  │
  ├── SessionStart 훅        → csm hook session-start   (레코드 등록, /dev/tty에
  │                                                      창 제목 csm:<short-id> 스탬프)
  └── UserPromptSubmit 훅    → csm hook activity        (last_activity_at 갱신,
                                                         last_user_prompt 저장)

~/.claude/claude-tasks/
  └── <session-uuid>.json      세션당 파일 1개, 원자적 쓰기

csm scan                       ~/.claude/projects/*/transcripts/*.jsonl 읽어
                               꼬리 50줄에서 진행 필드 추출 ("fresher wins")

csm list / watch / statusline  레지스트리에 대한 읽기 전용 뷰
csm focus / resume             AppleScript / terminal CLI로 창 제어
```

훅은 항상 exit 0. 실패 내용은 `~/.claude/claude-tasks/.hook-errors.log`에 타임스탬프와 함께 기록되므로 훅 문제가 Claude Code 자체를 막지 않는다.

## 플랫폼 지원

| 서브명령 | macOS | Linux | Windows |
|---|---|---|---|
| `list`, `set`, `done`, `archive`, `scan`, `statusline`, `gc`, `review-stale`, `watch` | ✓ | ✓ | ✓ |
| `focus` | ✓ | ✓ (X11/Wayland 제목 매칭) | exit 6 |
| `resume` | ✓ | ✓ (WezTerm/Kitty 한정) | exit 6 |

Live-vs-idle 도트는 `ps` 파싱 실패 시 `○`로 안전하게 degrade한다.

## 제거

```bash
rm ~/.local/bin/csm
rm ~/.claude/skills/claude-session-manager
rm ~/.claude/commands/{tasks,task-*,done}.md
# ~/.claude/settings.json에서 SessionStart / UserPromptSubmit 엔트리 제거
# 레지스트리까지 지우려면:
rm -rf ~/.claude/claude-tasks
```

## 라이선스

MIT
