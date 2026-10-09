# Coding standards

Read during review. Mechanical rules live in `pyproject.toml` (ruff, basedpyright) and run in `scripts/check`, so they are not repeated here. These are the judgement calls no tool can make. When a review catches the same mistake twice, move it into a tool if possible.

## Invariants (a violation is always blocking)

1. **Nothing sensitive in the repo.** No credentials, tokens, endpoint addresses or hostnames, email addresses, machine-specific paths or personal content in any tracked file, test, fixture or doc. The repo is public and its history is permanent.
2. **Cloud only.** No code for local or Mac use, no local database login, no operating-system credential store.
3. **The pin is a full commit hash.** Setup fetches this repo at a full 40-hex hash and verifies the checked-out commit equals it before installing anything. A branch name, tag or short hash is refused.
4. **Cloud startup fails closed.** A missing, crashing, empty, malformed, unauthorized or timed-out client, hook or endpoint blocks work with a plain reason. It never continues silently and never claims success it has not confirmed.
5. **No automatic retry or replay of a memory write.** An unconfirmed outcome is reported as unknown and recovery needs an explicit successful index read.
6. **Failures never leak.** Error output is bounded and never contains the endpoint address, the Authorization value, the SQL or its parameter values.
7. **Install atomically and never destroy user files.** A bundle is validated whole, then moved into place; anything it replaces goes to a timestamped backup, never deleted. A refused bundle writes nothing. Reject absolute paths, `..`, symlinks, duplicates and anything outside the skills tree.
8. **Installers are idempotent.** A second run changes nothing and preserves unrelated settings.
9. **Tests are hermetic.** Every test sets HOME to a temporary directory and uses a fake endpoint or a local bare repository. Nothing reads or writes the real home directory, contacts a real endpoint or uses a real token.
10. **The lock is sacred.** Approved specs stay fixed from the start of a run. Locked acceptance tests change only through the independent writer's single repair packet, preserving every observable obligation.
11. **Standard library only.** No third-party runtime dependency; tool versions are pinned in `scripts/check`.

## Judgement rules

- Comments explain why, not what. Names say what a thing is for.
- Small functions with one reason to change; no dead code or speculative options.
- Prefer a refusal with a clear reason over a clever fallback.
