# Context

Words used in this repo, taken from the approved spec in `docs/specs/`.

- **Cloud session**: one run of an agent (Claude or Codex) in a provider's managed cloud environment.
- **Setup phase**: the provider's pre-session step; it has no credentials, so nothing authenticated can happen in it.
- **Memory endpoint**: the authenticated HTTPS service that answers memory queries and serves the bundle. Its address and credentials are never in this repo.
- **Client**: the command (`ail-memory`) that sends one memory request and returns a structured result or a bounded failure.
- **Memory index**: the short summary of what memory holds, read once at session start.
- **Startup check**: the session-start step that loads the memory index first, then installs the bundle; any failure blocks work.
- **Bundle**: the skills files and cloud rules the endpoint serves for a session to install.
- **Stale bundle**: the last good bundle, served with a notice when the source was unreachable.
- **Pin**: the full 40-character commit hash the setup script uses to fetch this repo; a branch or tag name is never a valid pin.
- **Adapter**: the provider-specific wiring (Claude hook, Codex hook) that runs the startup check.
- **Block**: stop further work and report a plain reason; the required response to any startup or memory failure in cloud.
