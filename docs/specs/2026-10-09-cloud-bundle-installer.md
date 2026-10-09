---
id: 2026-10-09
status: approved
repo: ail-cloud-bootstrap
approved_by: Francis
approved_at: 2026-10-09
lane: full
lane_reason: cloud credentials and authentication handling, and code that runs at the start of every cloud session
---

# ail-cloud-bootstrap: the public cloud memory installer, client and startup

## Brief (for Francis)

This new public repo is the only home of the small code that gives a cloud agent session its memory: the memory client, the session-start check, the fetcher that installs skills and rules from the memory endpoint, and the installer that wires them in. It contains no secrets and nothing personal. The cloud environment's setup script fetches it at a pinned commit, so setup needs no GitHub credentials and works on any repository.

```text
cloud setup (no credentials):  fetch this repo @ pinned SHA --> install client + hook
session start:                 memory index (hard stop on failure)
                               fetch skills + rules from the endpoint --> install atomically
```

**You'll know it works when:**
1. Setup on an empty machine with no GitHub access succeeds from the pinned SHA, and refuses a wrong or unpinned reference.
2. A session start loads the memory index first, then installs the skills and rules; any failure blocks work with a plain reason.
3. A bundle with a bad path, a symlink, a duplicate or an oversize payload is refused and writes nothing.
4. A scan of every file in this repo finds no credentials, no personal content and no machine-specific paths.

**Needs from you:** approve this brief; approve publishing the first commit to the public repo (this spec is its first file); later, put the pinned installer line into the cloud environment's setup script.
**Risk:** a public repo's history cannot be recalled. Blast radius: every cloud session's startup. Code here runs in cloud sessions, so the pin must never be a branch name.

## Problem

The existing cloud installer lives in a private repo and clones it over HTTPS during setup, which fails in cloud because setup has no credentials. The authenticated memory endpoint cannot be used during setup either, because the proxy-injected credential is not available to setup scripts. This repo holds the code that needs to be public, and nothing else.

This file is this repo's companion to the overall contract in the `ail-context` repository (`docs/specs/2026-10-09-cloud-bundle-installer.md`, approved 2026-10-09), which has the full decisions and the endpoint side. A private companion covers the rules and skills side.

## Decisions locked

1. This repo is the only home of the memory client (`ail-memory`), the cloud session-start hook, the startup check, the bundle fetcher and the cloud installer. The private repo keeps no copy.
2. The cloud environment's setup script obtains this repo at a pinned full 40-hex commit SHA and verifies the checked-out commit equals the pin before installing anything. Branch names, tags and short SHAs are refused.
3. This repo contains no secrets, no credentials, no endpoint address, no personal content, no machine-specific paths and no code for local or Mac use. It is cloud-only.
4. The client keeps the existing v1 contract unchanged: one JSON request on stdin with `version`, `sql`, `params`; HTTPS only; no redirects; no automatic retries; the 65,536-byte request limit and the 1 MiB response limit for memory queries; finite timeout from 1 to 60 seconds; failures as bounded v1 JSON on stderr that never expose the URL, the Authorization value, the SQL or parameter values.
5. Session start order: memory index first (a failure blocks, as today), bundle second. A stale bundle continues with a visible notice. A bundle failure (no good copy ever stored, invalid, oversize or hostile) blocks.
6. The bundle is installed atomically: all files are written to a temporary location and moved into place only after the whole bundle validates. A refused bundle writes nothing. Existing files that would be replaced are moved to a timestamped backup, never deleted.
7. Provider-neutral shared parts. The Claude wiring is built and proven first. The Codex wiring is written and tested against a fake endpoint, and marked unproven until a real Codex cloud environment passes.
8. Recovery after a block requires an explicit successful authenticated index read through the client, as today. No automatic replay, retry or offline queue.
9. Full lane. Test writers may replace obsolete expectations that moved here from the private repo before locking; builders cannot edit locked tests.

## Scope

### In

- Repository scaffold: `scripts/check`, `.sdlc.json`, tests folder, CI workflow, README with the activation and rollback notes (the pinned installer line, how to move the pin).
- The client, session-start hook, startup check, bundle fetcher and installer for Claude and Codex, with tests moved or rewritten from the private repo.
- A scan test that guards decision 3.

### Out

- Any Mac or local-use code, the personal login, the memory database tools.
- The endpoint, the migration and the rules/skills content.
- Any real endpoint address, token or account detail in code, tests or docs.

## Acceptance scenarios

1. **Setup without credentials.** Given an empty HOME, no private repository, no GitHub credentials and no network access to the endpoint, when the setup script runs the installer from a local bare repository standing in for this repo at the pinned SHA, then it exits 0, installs the client and the Claude SessionStart hook, makes no authenticated request, and a second run changes nothing.
2. **Pin enforced.** Given a pin that is not a full 40-hex SHA, or a pin that does not match the fetched commit, or a branch or tag name, when setup runs, then it exits non-zero and installs nothing.
3. **Startup order and blocking.** Given a fake endpoint and each adapter (Claude, Codex), when a session starts, then the index is requested first and the bundle second; skills land in the agent's skill directories (`~/.claude/skills` for Claude; `~/.codex/skills` and `~/.agents/skills` for Codex); the cloud rules reach the agent as startup context; if the index fails, nothing else is requested and startup blocks; a harmless follow-on work marker never runs after a block.
4. **Stale and never-fetched.** Given a fake endpoint returning `stale: true`, startup continues and shows the stale notice with the last good fetch time. Given the endpoint reporting no good bundle, startup blocks with a reason and installs nothing.
5. **Hostile or invalid bundle.** Given a bundle containing an absolute path, a `..` segment, a symlink, a duplicate path, a path outside the skills tree, malformed JSON, an unknown bundle version, or a size above the documented bundle limit, when the fetcher installs it, then nothing is written, startup blocks with the reason, and no content is truncated.
6. **Atomic install and backup.** Given an existing skill directory that the bundle would replace, when install succeeds, then the old directory is moved to a timestamped backup and the new files are in place; when a failure occurs mid-install, the previous state is intact and no partial skill remains.
7. **Client contract preserved.** Given the transport tests moved from the private repo, when they run against the client here, then they pass unchanged: request and response limits, non-finite and out-of-range timeouts, redirect refusal, no retries, no leakage of URL, Authorization, SQL or parameters, `unknown`, `not_executed` and `rolled_back` outcomes kept distinct.
8. **Fails closed when a part is missing.** Given the client or the hook missing, crashing, returning an empty or malformed index, timing out or unauthorized, then startup blocks with a plain reason and the follow-on marker does not run.
9. **Public repo hygiene.** Given every tracked file, when the scan runs, then it finds no credential-shaped strings (GitHub token prefixes, bearer values, long hex or base64 secrets), no hostnames other than the git host used for the pin tests, no email addresses, no absolute home-directory paths, and no references to the local-use database wrapper or the operating-system credential store (the exact forbidden patterns live in the scan test, not in prose).
10. **Codex wiring is tested but unproven.** Given the Codex adapter and a fake endpoint, the same startup, bundle and block scenarios pass; the README states Codex as unproven until a genuine session passes.

## Seam

Tests run as subprocesses in a temporary HOME with a local bare git repository standing in for this repo (the pin tests) and a fake HTTP endpoint (the startup, stale, hostile and contract tests). Catch: pin enforcement, ordering, blocking, atomicity, hostile input, the client contract and leakage. Miss: whether a managed cloud host actually runs the SessionStart hook, whether skills written after startup are discovered in the same session, and whether the proxy injects the credential on the bundle route. Genuine-session evidence from the overall contract fills those. Local subprocess tests prove only controlled adapter behaviour; they cannot prove that a managed host stops an agent.

## Data

None. The bundle format version 1 is defined by the endpoint's tests and mirrored here: `{version, commit, fetched_at, stale, stale_reason, rules, files:[{path, content}]}` where `path` is a relative POSIX path under the skills tree and `content` is the file text (binary files are not supported in version 1; a bundle containing one is refused). An unknown version is refused.

## Preflight

Commands run from the repo root.

```preflight
public repo exists and is public :: test "$(gh repo view FrancisMarzynski/ail-cloud-bootstrap --json visibility -q .visibility)" = PUBLIC
GitHub CLI authenticated :: gh auth status
uv installed :: command -v uv
on main :: test "$(git branch --show-current)" = main
overall contract approved :: grep -q '^status: approved' "$HOME/Downloads/ail-context/docs/specs/2026-10-09-cloud-bundle-installer.md"
```

The repo has no `scripts/check` yet; creating it is the first build step (see Approach), so "tests pass on main" cannot be a preflight item here.

## Approach

First build step: scaffold the SDLC gate for this repo (a `scripts/check` that runs lint, types, shell syntax and the tests; `.sdlc.json`; CI), using the standard repo setup templates. Then port the cloud client, the cloud branch of the session-start hook and the startup check from the private repo (their tests move with them), add the bundle fetcher and the installer for Claude and Codex, then the hygiene scan. Keep dependencies to the standard library.

## Integration (when coupled)

One implementation owner across the three repositories; this repo is built second, after the endpoint's bundle route and shape are fixed by its tests. The pin that the setup script uses is the SHA of this repo's reviewed `main` commit and is updated deliberately. The private repo's removal of its own copy waits for the genuine-session proof. Rollback: the setup script's pin line is removed or reverted to the previous pin; nothing in this repo is needed by the Mac.

## Risks and open questions

- Code in a public repo runs in every cloud session. Mitigations: a full-SHA pin with verification, atomic install, no secrets, and the hygiene scan. A compromised account could still publish malicious code at a new SHA; changing the pin is a deliberate manual edit.
- Once pushed, history on a public repo is permanent. The first commit is this spec, which contains no secrets or personal content by construction; no commit may contain real endpoint addresses or tokens.
- Skills written after session start may not be discovered in the same session. The overall contract's fallback applies and the genuine-session check settles it.
- The bundle size limit must be set well above the current bundle size and documented; the builder records both numbers.
- Binary files in skills are unsupported in bundle version 1. If a skill ever needs one, the version is bumped deliberately.
