#!/usr/bin/env bash
# Cloud session start: bash cloud/startup.sh <claude|codex> [-- command ...]
#
# 1. memory index (hooks/session_index.py, through bin/ail-memory); 2. skills and rules
# bundle (hooks/cloud_bundle.py). Any failure blocks: non-zero exit, a short reason on
# stderr, and the optional continuation never runs. Each part's own stderr is kept back
# and only its single `ail-cloud:` reason line is shown, so stray output from a broken part
# cannot leak the endpoint address, the Authorization value or a request.
# bash 3.2 compatible (macOS): no mapfile, no associative arrays.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
recovery='Stop work; recovery requires a successful authenticated index check.'

block() {
  printf 'Cloud memory startup blocked: %s. %s\n' "$1" "$recovery" >&2
  exit 2
}

case "${1-}" in
  claude) export CLAUDE_CODE_REMOTE=true; unset CODEX_CLOUD ;;
  codex) export CODEX_CLOUD=1; unset CLAUDE_CODE_REMOTE ;;
  *) block 'expected claude or codex as the provider' ;;
esac
provider="$1"
shift
if [[ $# -gt 0 ]]; then
  [[ "$1" == -- && $# -gt 1 ]] || block 'invalid continuation (expected -- command ...)'
  shift
fi

errors="$(mktemp -d "${TMPDIR:-/tmp}/ail-cloud-startup.XXXXXX")" || block 'cannot create a temporary directory'
trap 'rm -rf "$errors"' EXIT

# The first `ail-cloud:` line a part wrote, bounded; never anything else it printed.
reason() {
  local line
  line="$(sed -n 's/^ail-cloud: //p' "$1" 2>/dev/null | head -n 1 | cut -c 1-300)" || line=''
  printf '%s' "${line:-no reason given}"
}

if ! index="$(python3 "$repo/hooks/session_index.py" </dev/null 2>"$errors/index")"; then
  block "memory index unavailable: $(reason "$errors/index")"
fi
[[ -n "$index" ]] || block 'memory index unavailable: the index check printed nothing'

if ! context="$(printf '%s' "$index" | python3 "$repo/hooks/cloud_bundle.py" "$provider" 2>"$errors/bundle")"; then
  block "bundle step failed: $(reason "$errors/bundle")"
fi
[[ -n "$context" ]] || block 'bundle step failed: the bundle installer printed nothing'

printf '%s\n' "$context"
rm -rf "$errors"
trap - EXIT
if [[ $# -gt 0 ]]; then exec "$@"; fi
