# Claude Session Manager — Design Spec

**Date**: 2026-04-14
**Status**: Draft (awaiting user review)

## Problem

사용자가 여러 터미널에서 Claude Code 세션을 동시에 띄워 작업하다 보면:

1. 창이 많아져 "지금 어떤 작업 중인 창"인지 찾기 어렵다.
2. 작업 간 우선순위가 뒤섞인다.
3. 완료하지 못한 창이 계속 쌓여 정리되지 않는다.

이 세 문제를 하나의 경량 로컬 도구로 해결한다.

## Goals / Non-goals

**Goals**
- 동시에 열려 있는 모든 Claude Code 세션을 한 곳에서 조회.
- 세션별 제목, 우선순위, 상태, 마지막 활동 시각, 메모를 추적.
- "그 창으로 이동" 한 동작으로 원하는 세션의 터미널 창을 앞으로 가져오기.
- 유휴가 길어진 세션(stale)을 감지하고 사용자가 triage.
- Claude Code 내부(슬래시 명령, 상태바)와 외부(CLI, TUI) 양쪽에서 동일한 데이터 접근.

**Non-goals**
- 다중 머신/원격 동기화.
- 웹 UI.
- 세션 간 의존성/블로킹 그래프.
- 데스크톱 푸시 알림.
- 자동 파괴적 정리(자동 삭제 금지, 삭제는 항상 사용자 확인).

## Architecture Overview

```
~/.claude/skills/claude-session-manager/
├── SKILL.md                      # 스킬 진입점 / 트리거 설명
├── scripts/
│   ├── cst.py                    # CLI + TUI 진입점 (argparse 기반)
│   ├── registry.py               # 세션 레지스트리 CRUD
│   ├── scanner.py                # ~/.claude/projects/ 자동 스캔
│   ├── focus.py                  # macOS 창 포커싱 (osascript)
│   ├── statusline.py             # 상태바용 경량 출력
│   └── hooks.py                  # SessionStart / UserPromptSubmit 훅 로직
├── commands/                     # Claude Code 슬래시 명령 정의
│   ├── tasks.md
│   ├── task-register.md
│   ├── task-note.md
│   ├── task-priority.md
│   ├── task-status.md
│   ├── task-done.md
│   └── task-focus.md
└── install.sh                    # 심볼릭 링크/훅/statusLine 설정

~/.claude/claude-tasks/           # 런타임 데이터 (세션당 파일 1개)
└── <session-id>.json
```

**진입점은 세 가지이고 모두 같은 Python 모듈을 공유:**
1. `cst` CLI/TUI (외부 터미널).
2. 6개의 슬래시 명령 (Claude Code 내부).
3. Claude Code 훅(`SessionStart`, `UserPromptSubmit`)과 `statusLine`.

## Data Model

**파일 1개 = 세션 1개.** 동시 쓰기 충돌 최소화, 고장 격리.

```json
{
  "session_id": "abc123-def456",
  "title": "로그인 API 리팩토링",
  "priority": "high",
  "status": "in_progress",
  "cwd": "/Users/uni4love/project/foo",
  "project_name": "foo",
  "tags": ["backend", "urgent"],
  "note": "Redis 연결 끊김 이슈 조사 중",
  "created_at": "2026-04-14T10:23:00Z",
  "last_activity_at": "2026-04-14T14:52:11Z",
  "terminal": {
    "app": "iTerm2",
    "window_id": "w1",
    "tab_id": "t2",
    "tty": "/dev/ttys003"
  },
  "auto_detected": true,
  "archived": false,
  "archived_at": null
}
```

**필드 규칙**
- `session_id`: Claude Code 실제 세션 UUID. 훅/스캐너가 주입.
- `priority`: `high | medium | low`.
- `status`: `in_progress | blocked | waiting | done | stale`.
  - `stale`은 스캐너가 자동 부여 (유휴 4h 초과 + 아직 `done/archived` 아님).
  - `done`은 사용자 명시 완료.
- `auto_detected=true`는 스캐너 초안. 사용자가 `/task-register`나 `cst set`으로 필드를 건드리면 `false`로 전환.
- `archived=true`는 목록에서 숨김. `cst list --all`에서만 보임. 파일은 `cst gc`가 7일 후 삭제.
- `terminal.app`이 `Ghostty/Alacritty` 등 AppleScript 미지원 앱이면 `window_id/tab_id`는 `null`, `tty`만 저장. 포커싱은 "지원 안 됨"으로 표시.

## Entry Points

### CLI (`cst`)

| 명령 | 동작 |
|---|---|
| `cst list` | 활성 세션 테이블. 정렬: priority desc → last_activity desc. |
| `cst list --all` | 아카이브 포함. |
| `cst list --stale` | stale만. |
| `cst watch` | rich 기반 TUI. 2초 갱신. 키: ↑↓ 이동, Enter 포커싱, `n` 노트, `p` priority, `s` status, `d` done, `a` archive, `r` resume, `q` quit. |
| `cst watch --pin` | iTerm2 창을 지정 위치/크기로 생성해 watch 실행. |
| `cst focus <id>` | 저장된 터미널 창 포커싱. 창을 못 찾으면 `resume` 제안. |
| `cst resume <id>` | 새 터미널 창에서 `cd <cwd> && claude --resume <session-id>`. |
| `cst register` | 대화형 등록 (제목/priority/tags). 현재 세션을 env로 식별. |
| `cst set <id> [--title ...] [--priority ...] [--status ...] [--note ...] [--tags ...]` | 필드 갱신. |
| `cst done <id>` / `cst archive <id>` | 완료/아카이브. |
| `cst scan` | 강제 재스캔. |
| `cst review-stale` | stale 항목 하나씩 제시, `[k]eep / [d]one / [a]rchive / [s]kip`. |
| `cst gc` | `archived && archived_at > 7d` 레지스트리 파일 삭제. |

**ID 입력 규칙**: session_id 앞 6자 프리픽스 허용. 중복 시 후보 목록 제시 후 종료.

### Slash commands (Claude Code 내부)

| 명령 | 동작 |
|---|---|
| `/tasks` | `cst list` 출력을 채팅에 표시. 번호 인덱스 포함. |
| `/task-register [제목] [priority]` | 현재 세션을 등록. `$CLAUDE_SESSION_ID`로 식별. |
| `/task-note "<text>"` | 현재 세션 note 업데이트. |
| `/task-priority high\|medium\|low` | 현재 세션 priority 변경. |
| `/task-status in_progress\|blocked\|waiting` | 상태 변경. |
| `/task-done` | 현재 세션 완료. |
| `/task-focus <번호\|id>` | 해당 세션 창 포커싱. |

`/tasks` 출력 예시:
```
[1] ●high   로그인 API 리팩토링       foo    2h ago
[2] ○med    대시보드 차트 교체        bar    15m ago
[3] ●high   결제 웹훅 디버깅 (stale)  baz    6h ago
→ /task-focus <번호>
```
`●`는 "지금 실제로 돌고 있는 프로세스 있음"(ps + tty 매칭), `○`는 유휴.
번호는 현재 목록의 안정 인덱스 (요청 시점 기준).

### Statusline

`~/.claude/settings.json`의 `statusLine.command`를 `cst statusline` 호출로 설정.
출력 형식:
```
📋 3 pending · 1 stale  →  /tasks
```
- 50ms 이내 반환 목표.
- 레지스트리 디렉토리 스캔만 수행, 네트워크/무거운 파싱 금지.
- `pending` = `status ∈ {in_progress, blocked, waiting}` && `!archived`.
- `stale` 개수는 0이면 표시 생략.

**제약 명시**: Claude Code statusLine은 텍스트 출력만 지원하며 클릭/선택은 불가. 사용자는 `/tasks`로 목록을 펼치고 `/task-focus <번호>`로 이동한다. "클릭-like" 경험이 필요하면 `cst watch --pin`을 병행한다.

### Hooks

`install.sh`가 `~/.claude/settings.json`의 `hooks`에 병합:
- **SessionStart**: `cst hook session-start` → 현재 세션의 터미널(app/window/tab/tty) 캡처 후 레지스트리에 기록/갱신.
- **UserPromptSubmit**: `cst hook activity` → `last_activity_at`만 touch. 실패해도 사용자 흐름을 막지 않음(exit 0 보장).

기존 훅이 있으면 덮어쓰지 않고 배열에 추가. 중복 엔트리 방지.

## Key Flows

### 1. 신규 세션 등장
1. 사용자가 새 터미널에서 `claude` 실행.
2. `SessionStart` 훅이 `cst hook session-start` 호출.
3. `cst`가 `$CLAUDE_SESSION_ID`, `$PWD`, 현재 터미널 정보(frontmost app, window id, tty)를 수집해 레지스트리에 `auto_detected=true` 레코드 생성. `title`은 잠정적으로 프로젝트명.
4. 사용자가 `/task-register "제목" high`로 확정하면 `auto_detected=false`.

### 2. 창 찾기 (가장 중요한 흐름)
1. 사용자가 Claude Code 채팅 하단 상태바에서 `📋 3 pending` 확인.
2. `/tasks` 입력 → 번호 매겨진 목록 표시.
3. `/task-focus 2` → `cst focus <id>` 실행 → osascript가 저장된 app/window/tab를 맨 앞으로.
4. 창이 닫혔거나 AppleScript 실패 → `cst resume <id>` 제안 및 안내.

### 3. Stale triage
1. `cst list`가 실행될 때마다 `last_activity_at > 4h`인 항목을 `stale`로 마킹.
2. 목록 하단 배너: `⚠ 3 stale sessions — run 'cst review-stale'`.
3. `cst review-stale`: 항목마다 `[k]eep / [d]one / [a]rchive / [s]kip`. 자동 삭제 없음.

### 4. 상시 TUI (옵션)
1. 작업 창이 많은 날 사용자가 `cst watch --pin` 실행.
2. iTerm2 전용 창이 화면 구석에 생성되어 실시간 목록 표시.
3. 그 창 안에서 ↑↓ + Enter로 대상 창 포커싱.

## Scanner Behavior

`~/.claude/projects/<project-slug>/<session-id>.jsonl` 구조를 전제한다.

1. 모든 `*.jsonl` 순회. 파일명에서 `session_id` 추출.
2. 레지스트리에 없는 세션:
   - `title`: 첫 user 메시지 60자 요약 (없으면 프로젝트명).
   - `cwd`: 프로젝트 디렉토리로 역추적 (프로젝트 slug → 실제 경로는 JSONL 메타에서 확보 가능, 없으면 null).
   - `last_activity_at`: 파일 mtime.
   - `auto_detected=true`.
3. 이미 있는 세션: `last_activity_at`만 갱신.
4. 유휴 > 4h && `status ∈ {in_progress, blocked, waiting}` → `status = stale`.
5. `done` 또는 `archived`는 스캐너가 절대 덮어쓰지 않음.

**호출 시점**: `cst list`, `cst watch` 한 프레임, 슬래시 명령 시작 시 자동. 마지막 스캔이 30초 이내면 스킵.
강제 실행은 `cst scan`.

**활성 여부 판별**: `ps -ef | grep -E 'claude( |$)'` 결과를 파싱해 tty와 매칭. 매칭되면 `●`, 아니면 `○`.

## Focus Module

| 터미널 앱 | 지원 방식 |
|---|---|
| iTerm2 | AppleScript: `tell application "iTerm2" to select window id <wid>` |
| Terminal.app | AppleScript: window id + tab index |
| Ghostty / Alacritty / WezTerm | 미지원 (tty만 저장, focus 시도 시 "supported=false" 안내) |

실패 시 항상 `cst resume <id>` 대안 제시. focus는 파괴적 동작이 없으므로 실패도 안전.

## Install

`install.sh` 단계:
1. `scripts/cst.py`에 `+x`.
2. `~/.local/bin/cst` 심볼릭 링크 생성. PATH에 없으면 stdout에 안내.
3. `python3 -c 'import rich'` 확인, 없으면 `pip install --user rich` **제안**(자동 실행 안 함).
4. `~/.claude/claude-tasks/` 디렉토리 생성.
5. `~/.claude/settings.json` 병합:
   - `hooks.SessionStart` / `hooks.UserPromptSubmit`에 `cst hook ...` 추가 (중복 시 skip).
   - `statusLine.command`가 비어 있으면 `cst statusline`으로 설정. 이미 값이 있으면 경고 + 수동 통합 안내.
6. `cst list`로 smoke test.

모든 단계는 idempotent. 재실행 시 부작용 없음.

## Error Handling Principles

- 레지스트리 I/O 실패는 사용자 흐름을 막지 않는다. 훅은 항상 `exit 0`.
- 손상된 JSON 파일은 `<id>.json.corrupt-<timestamp>`로 이름 바꿔 격리하고 계속 진행.
- `cst` 명령은 실패 시 명확한 메시지 + exit code ≥1.
- osascript 실패는 사용자에게 `resume` 대안을 반드시 안내.

## Testing

**pytest 단위 테스트**
- `registry.py`: tmp 디렉토리에서 create / read / update / delete round-trip, 손상 파일 격리, 동시성(두 프로세스 동시 쓰기).
- `scanner.py`: fixture JSONL 집합으로 초안 생성, 기존 세션 mtime 갱신, stale 전이, done/archived 미변경.
- `focus.py`: subprocess 모킹으로 osascript 명령 문자열만 검증. 실제 실행은 macOS 수동 테스트.
- `statusline.py`: 다양한 레지스트리 상태에서 출력 포맷 / 실행 시간(<100ms) 검증.

**수동 검증 체크리스트 (install.sh 후)**
- [ ] 두 개 창에서 Claude Code 실행 → 각각 `/task-register` → `cst list`에 2개.
- [ ] 한 창에서 `cst focus <other-id>` → 다른 창이 앞으로.
- [ ] 상태바에 `📋 2 pending` 표시.
- [ ] 한 창에서 `/tasks` → 번호 목록 → `/task-focus 2` → 창 전환.
- [ ] fixture로 mtime을 5시간 전으로 조작 → `cst list`에 stale 배너.
- [ ] `cst review-stale` → 선택지 동작.
- [ ] `cst watch --pin` → iTerm 새 창에서 실시간 UI.
- [ ] `cst gc` → 7일 이상 archived 파일 삭제 확인.

## YAGNI — 일부러 제외

- 원격/멀티머신 동기화
- 웹 UI
- 세션 간 의존성 그래프
- 데스크톱 푸시 알림
- 자동 삭제 (모든 삭제는 사용자 확인 필수)

## Open Questions (구현 단계에서 확정)

1. `cst` 심볼릭 링크 설치 경로 기본값: `~/.local/bin/cst` vs `/usr/local/bin/cst`. 전자로 진행 예정.
2. stale 임계값 기본 4h — 설정 파일(`~/.claude/claude-tasks.config.json`) 한 줄로 오버라이드 허용.
3. `$CLAUDE_SESSION_ID`가 실제 환경에 항상 노출되는지 확인 필요. 없으면 fallback은 `tty` + `~/.claude/projects/` mtime 매칭.
