# ail-cloud-bootstrap

The small, public, cloud-only bootstrap that gives a cloud agent session its memory: the memory client, the session-start check, the bundle fetcher and the installer. No secrets and no personal content live here, and this repo has no Mac or local-use code.

## Verify

- `scripts/check` is the only verification command: lint, shell syntax, types and every test. CI runs the same script. A Stop hook runs it before any agent turn ends in this repo.
- Never weaken a check to get green. Tests never touch the real home directory, a real endpoint or a real token; they use a temporary HOME, a fake endpoint and a local bare git repository.

## Work

This repo uses the AIL SDLC (`/grill` → `/spec` → `/ship`). Specs live in `docs/specs/`. Run config: `.sdlc.json`. Full locked acceptance tests live in `tests/acceptance/`; only the independent writer corrects them.

Every file here is public and its history is permanent: never commit a credential, an endpoint address, an email address, a machine-specific path or personal content.

## Pointers

- Domain words: `CONTEXT.md`
- Review rules (read by reviewers, not needed while building): `CODING_STANDARDS.md`
- Black-box test style to copy: `tests/test_repo_contract.py`
