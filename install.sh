#!/usr/bin/env bash
# Idempotent installer for the claude-session-manager skill.
#
# Behavior (binding, per sprint_contract.md §2):
# - Creates ~/.claude/skills/claude-session-manager as a symlink to $(pwd).
# - Creates ~/.local/bin/cst as a symlink to scripts/cst.py (chmod +x).
# - Creates ~/.claude/ and ~/.claude/claude-tasks/ if missing.
# - Merges exactly-one SessionStart and UserPromptSubmit hook entry into
#   ~/.claude/settings.json; existing statusLine is never touched.
# - If ~/.claude/settings.json exists but is not valid JSON, exits with
#   code 2 and does NOT modify or back up the file (policy (a)).
# - Runs `cst list` as a smoke test at the end; nonzero on failure.

set -u  # NOT -e: we handle failures explicitly.

SKILL_DIR="$(cd "$(dirname "$0")" && pwd -P)"
CLAUDE_DIR="${HOME}/.claude"
SKILLS_DIR="${CLAUDE_DIR}/skills"
TASKS_DIR="${CLAUDE_DIR}/claude-tasks"
BIN_DIR="${HOME}/.local/bin"
CST_BIN="${BIN_DIR}/cst"
SKILL_LINK="${SKILLS_DIR}/claude-session-manager"
SETTINGS="${CLAUDE_DIR}/settings.json"

# Refuse up-front if ${CST_BIN} is a regular file (not a symlink). We
# check BEFORE creating any directories so a failed install leaves the
# filesystem untouched.
if [ -e "${CST_BIN}" ] && [ ! -L "${CST_BIN}" ]; then
    echo "cst install: ${CST_BIN} exists as a regular file; refusing to overwrite. Remove it or move it aside, then rerun." >&2
    exit 3
fi

mkdir -p "${SKILLS_DIR}" "${TASKS_DIR}" "${BIN_DIR}"

# Skill symlink (idempotent)
ln -sfn "${SKILL_DIR}" "${SKILL_LINK}"
echo "cst install: skill linked at ${SKILL_LINK} -> ${SKILL_DIR}"

# cst entry symlink (idempotent; broken-symlink case handled by ln -sfn)
chmod +x "${SKILL_DIR}/scripts/cst.py"
ln -sfn "${SKILL_DIR}/scripts/cst.py" "${CST_BIN}"
echo "cst install: cst linked at ${CST_BIN}"

# PATH advisory (non-fatal)
case ":${PATH}:" in
    *":${BIN_DIR}:"*) : ;;
    *)
        echo "cst install: NOTE — ${BIN_DIR} is not in \$PATH."
        echo "cst install: add it to your shell init, e.g.:"
        echo "    export PATH=\"${BIN_DIR}:\$PATH\""
        ;;
esac

# Slash commands: symlink each commands/*.md into ~/.claude/commands/
# (idempotent — ln -sfn overwrites our own prior symlinks but will not
# touch a user's unrelated regular file of the same name).
COMMANDS_DIR="${CLAUDE_DIR}/commands"
mkdir -p "${COMMANDS_DIR}"
for src in "${SKILL_DIR}"/commands/*.md; do
    [ -e "${src}" ] || continue
    name="$(basename "${src}")"
    dst="${COMMANDS_DIR}/${name}"
    if [ -e "${dst}" ] && [ ! -L "${dst}" ]; then
        echo "cst install: WARNING — ${dst} exists as a regular file; leaving it alone." >&2
        continue
    fi
    ln -sfn "${src}" "${dst}"
done
echo "cst install: slash commands linked into ${COMMANDS_DIR}"

# Settings merge (delegated to Python)
python3 "${SKILL_DIR}/scripts/installer.py" merge-settings
rc=$?
if [ "${rc}" -ne 0 ]; then
    echo "cst install: FAILED merging settings.json (exit ${rc})" >&2
    exit "${rc}"
fi

# Smoke test: run `cst list` via its absolute path so PATH doesn't matter.
if "${CST_BIN}" list >/dev/null; then
    echo "cst install: smoke test PASSED (cst list exit 0)"
else
    rc=$?
    echo "cst install: smoke test FAILED (cst list exit ${rc})" >&2
    exit "${rc}"
fi

echo "cst install: done"
exit 0
