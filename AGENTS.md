# ail-cloud-bootstrap

The small, public, cloud-only bootstrap that gives a cloud agent session its memory: the memory client, the session-start check, the bundle fetcher and the installer. No secrets and no personal content live here, and this repo has no Mac or local-use code.

## Work

This repo uses AIL SDLC v2. Read `ail-layer` and `poteto-mode` at the start of a job. Start through `router start` unless the router already holds the area for the job. Put the goal, what done means, and proof first in the PR after its ready line. Run `pr-queue check` before opening a PR. CodeRabbit reviews; Francis merges. Record decisions and events in memory, not Linear progress comments.

Risky paths: `bin/**`, `hooks/**` and `cloud/**`. For risky work, the builder sketches the design first, adds extra attack and verification, and Francis always merges. Old specs in `docs/specs/` and `tests/acceptance/` stay as history and tests.

Every file here is public and its history is permanent: never commit a credential, an endpoint address, an email address, a machine-specific path or personal content.

## Verify

- Run focused checks locally. `scripts/check` is the only complete verification command: lint, shell syntax, types and every test. CI runs the same script on the pushed commit. Configuration: `.check.json`.
- Never weaken a check to get green. Tests never touch the real home directory, a real endpoint or a real token; they use a temporary HOME, a fake endpoint and a local bare git repository.

## Pointers

- Domain words: `CONTEXT.md`
- Review rules (read by reviewers, not needed while building): `CODING_STANDARDS.md`
- Black-box test style to copy: `tests/test_repo_contract.py`
