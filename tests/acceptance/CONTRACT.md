# Acceptance seam (spec 2026-10-09)

The locked tests in this folder drive the code only through the interfaces below. The builder conforms to them; anything not named here is the builder's choice.

## Layout

| Path | What it is |
|---|---|
| `bin/ail-memory` | The memory client (executable). v1 contract unchanged: one JSON request on stdin, result on stdout, bounded v1 JSON failure on stderr. |
| `hooks/session_index.py` | Cloud-only memory index check (no local or Mac branch). |
| `hooks/cloud_bundle.py` | Bundle fetcher and atomic installer. |
| `cloud/startup.sh` | Session start: `bash cloud/startup.sh <claude\|codex> [-- <command> ...]`. |
| `cloud/setup.sh` | Setup-phase installer: `bash cloud/setup.sh <source> <pin>`. |

All credential-handling code lives under `bin/`, `hooks/` or `cloud/`.

## Environment

| Variable | Meaning |
|---|---|
| `AIL_MEMORY_URL` | Memory endpoint. HTTPS only; the bundle is `GET` on the same URL with `/bundle` appended to its path (`.../memory` gives `.../memory/bundle`). |
| `AIL_MEMORY_AUTHORIZATION` | Full header value. Sent unchanged on the index request and on the bundle request. When unset, only `CLAUDE_CODE_REMOTE=true` may proceed (the proxy injects the header); otherwise nothing is sent. |
| `AIL_MEMORY_TIMEOUT_SECONDS` | Finite, 1 to 60, default 15; bounds each request. |
| `AIL_MEMORY_ALLOW_LOOPBACK` | Test seam only: `1` allows `http://127.0.0.1:<port>`. |
| `CLAUDE_CODE_REMOTE`, `CODEX_CLOUD` | Provider markers; `startup.sh claude` sets `CLAUDE_CODE_REMOTE=true`, `startup.sh codex` sets `CODEX_CLOUD=1`. |

## Setup (`cloud/setup.sh <source> <pin>`)

- `<source>`: anything `git` can fetch from (the tests pass a local bare repository path).
- `<pin>`: a full 40-character lowercase hex commit hash. Anything else (short hash, branch, tag, a hash of a tag object or tree, a hash not in the source) exits non-zero, names the pin in the error, and changes nothing under `$HOME`.
- On success: the checkout at the pin lives in `$HOME/ail-cloud-bootstrap` (`git rev-parse HEAD` equals the pin); `$HOME/.local/bin/ail-memory` runs the client; `~/.claude/settings.json` has exactly one `SessionStart` command running `cloud/startup.sh` with `claude`; `~/.codex/config.toml` has exactly one `SessionStart` command running `cloud/startup.sh` with `codex`. Neither command swallows failure (`|| true`). Unrelated settings are preserved; a changed settings file is first copied to the backup root.
- Setup makes no request to the memory endpoint. A second run with the same pin changes nothing. Running with a new valid pin moves the checkout to it.

## Session start (`cloud/startup.sh <provider> [-- command ...]`)

1. `POST AIL_MEMORY_URL` with `{"version":1,"sql":"select memory.session_index()","params":[]}`. Any failure blocks; the bundle is never requested.
2. `GET AIL_MEMORY_URL/bundle`. Any failure blocks, except `stale: true`, which continues with a notice.
3. Validate the whole bundle, install it atomically, then print and continue.

Success: exit 0; stdout is one JSON object `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <text>}}` for both providers. `<text>` contains the index text and the bundle `rules` text, both whole. When the bundle is stale it also contains the word `stale` and the bundle's `fetched_at` value verbatim. Then the optional continuation runs.

Block: non-zero exit; the continuation never runs; stderr says `blocked` (or `stop`) in at most 4096 bytes and never contains the endpoint address, the Authorization value, SQL or parameters. A block caused by the bundle step also names `bundle`. A part (`bin/ail-memory`, `hooks/session_index.py`, `hooks/cloud_bundle.py`) that is missing, crashes, or exits 0 with no output blocks.

## Bundle (version 1, from the endpoint)

`{version, commit, fetched_at, stale, stale_reason, rules, files:[{path, content}]}`, exactly these keys, `version` the integer 1, each file exactly `{path, content}`.

- `path`: relative POSIX path starting `skills/<skill>/`, no empty, `.` or `..` segments, no backslash, no NUL, unique, and no path that is both a file and a directory of another entry. Anything else (including an entry with extra keys such as a link type or mode) refuses the whole bundle.
- `content`: a JSON string, written as UTF-8 bytes unchanged. It is refused if it contains U+0000 or cannot be encoded as UTF-8 (binary is not supported in v1).
- Response limit: **33,554,432 bytes (32 MiB)** of response body, eight times the endpoint's 4 MiB source limit, because JSON escaping can expand text up to six times. A larger or incompletely framed response is refused, never truncated.
- Endpoint refusals (`401 authentication`, `503 bundle_unavailable` with `error.reason`, `503 configuration`, `503 database_unavailable`), redirects, timeouts and malformed JSON block. Redirects are never followed.
- A refused bundle writes nothing under `$HOME`.

## Where skills land

`skills/<skill>/<rest>` is installed at `<root>/<skill>/<rest>` for each root: `~/.claude/skills` for Claude; `~/.codex/skills` and `~/.agents/skills` for Codex. A skill directory in the bundle replaces the whole existing directory of that name; directories not in the bundle are untouched. An existing directory being replaced is moved (never deleted) to `~/.ail-cloud-bootstrap-backup/<YYYYMMDD-HHMMSS>[suffix]/<path relative to $HOME>`. An existing symlink is replaced, never written through. Installing an identical bundle again changes nothing. If any step fails, every root is left as it was and startup blocks.
