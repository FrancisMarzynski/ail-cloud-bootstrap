# ail-cloud-bootstrap

The small public bootstrap that gives a cloud agent session its memory. It holds the memory client, the session-start check, the bundle fetcher and the installer, and nothing else. It contains no secrets and no personal content, and no code for local or Mac use.

See `docs/specs/` for the approved design and `CONTEXT.md` for the vocabulary.

## What is here

| Path | What it does |
|---|---|
| `bin/ail-memory` | The memory client (v1): one JSON request on stdin, the result on stdout, a bounded v1 JSON failure on stderr. HTTPS only, no redirects, no retries. |
| `hooks/session_index.py` | Session start, step 1: reads the memory index through the client. |
| `hooks/cloud_bundle.py` | Session start, step 2: fetches the skills and rules bundle, validates it whole and installs it atomically. |
| `cloud/startup.sh` | `bash cloud/startup.sh <claude\|codex> [-- command ...]`: runs both steps; any failure blocks. |
| `cloud/setup.sh` | `bash cloud/setup.sh <source> <pin>`: the setup-phase installer. |

## Activation (the pinned installer line)

The cloud environment's setup script runs `cloud/setup.sh` from this repository at a pinned commit. The pin is the full 40-character hash of a reviewed `main` commit; a branch name, tag or short hash is refused, and setup checks that the checked-out commit equals the pin before installing anything. Setup needs no credentials and makes no request to the memory endpoint.

```sh
PIN=<full 40-character commit hash of a reviewed main commit>
SRC=https://github.com/FrancisMarzynski/ail-cloud-bootstrap.git
dir="$(mktemp -d)" && git clone --quiet "$SRC" "$dir" \
  && git -C "$dir" -c advice.detachedHead=false checkout --quiet "$PIN" \
  && bash "$dir/cloud/setup.sh" "$SRC" "$PIN"
```

Setup installs the checkout at `~/ail-cloud-bootstrap`, links `~/.local/bin/ail-memory` to its client, and registers exactly one `SessionStart` hook that runs `cloud/startup.sh` for Claude (`~/.claude/settings.json`) and for Codex (`~/.codex/config.toml`). Unrelated settings are preserved. Anything it replaces is first moved or copied to `~/.ail-cloud-bootstrap-backup/<YYYYMMDD-HHMMSS>/`. Running it again with the same pin changes nothing.

The session needs `AIL_MEMORY_URL` (the endpoint, HTTPS). Codex also needs `AIL_MEMORY_AUTHORIZATION` (the full header value); in a Claude cloud session the provider proxy injects it. Neither value ever belongs in this repository.

**Moving the pin.** Review the new `main` commit, then change `PIN` in the setup script to its full hash. The next setup run moves the checkout to the new commit (the old checkout goes to the backup directory). Changing the pin is always a deliberate manual edit.

**Rollback.** Put the previous pin back in the setup script (or remove the installer line to turn cloud memory off). Nothing on the Mac depends on this repository.

## Provider status

- **Claude**: the wiring is built first and is the one to prove in a genuine cloud session.
- **Codex is unproven**: its wiring is written and tested against a fake endpoint, and stays unproven until a genuine Codex cloud session passes.

## Session start

1. The memory index is requested first. Any failure blocks, and the bundle is never requested.
2. The bundle is requested with `GET` on the endpoint URL with `/bundle` appended. Any failure blocks, except a stale bundle, which installs and continues with a notice that names the last good fetch time.
3. The whole bundle is validated, then installed: `skills/<skill>/...` lands in `~/.claude/skills` for Claude, and in `~/.codex/skills` and `~/.agents/skills` for Codex. A skill directory in the bundle replaces the existing directory of that name, which is moved to the backup directory; other skills are untouched. If any move fails, every change is moved back and startup blocks.

A block exits non-zero with a short reason on stderr, and the optional continuation never runs. Recovery needs an explicit successful index read; nothing is retried or replayed automatically.

## Bundle size numbers

- **Client limit: 33,554,432 bytes (32 MiB)** of response body (`BUNDLE_LIMIT` in `hooks/cloud_bundle.py`). It is eight times the endpoint's 4 MiB source limit, because JSON escaping can expand text up to six times. A larger or incompletely framed response is refused whole, never truncated.
- **Current bundle size: about 0.18 MiB** (188,630 bytes of response for 58 text files and 173,828 bytes of source), measured on 2026-10-09 from the skills source tree. Re-measure and update this line when the skills grow a lot.

## Bundle rules this installer enforces

Version 1 only, with exactly the keys `version, commit, fetched_at, stale, stale_reason, rules, files`, and each file exactly `{path, content}`. A bundle is refused whole when any of these holds:

- a path that is absolute, has an empty, `.` or `..` segment, a backslash or a control character, or is not inside `skills/<skill>/`;
- a file directly under `skills/` rather than inside a skill directory;
- a duplicate path, including paths that differ only by letter case or Unicode normalisation (common filesystems would merge them), or a path that is both a file and a directory;
- an entry with any extra key (such as a link type or a mode), content that is not text, or text containing NUL;
- an empty file list or empty rules (a source that lost its skills or rules must not pass silently);
- malformed JSON, duplicate JSON keys, non-finite numbers, or an unknown version.

A skill root (or a directory above it inside the home directory) that is a symlink refuses the install, so nothing is written through a link. A Claude session installs only into `~/.claude/skills`; it never fills the Codex directories.

## Verify

`scripts/check` runs lint, shell syntax, types and every test. Tests run in a temporary home directory against a fake endpoint and a local bare git repository.
