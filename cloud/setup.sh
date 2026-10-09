#!/usr/bin/env bash
# Setup-phase installer: bash cloud/setup.sh <source> <pin>
#
# <source> is anything git can fetch from (normally this repository's public URL);
# <pin> is the full 40-character lowercase commit hash to install. Branch names, tags,
# short hashes and hashes of anything but a commit are refused.
#
# The setup phase has no credentials, so this makes no request to the memory endpoint.
# The pin is fetched and verified in a temporary directory first; on any refusal nothing
# under $HOME changes. Then it installs:
#   $HOME/ail-cloud-bootstrap         the checkout at the pin (HEAD verified again)
#   $HOME/.local/bin/ail-memory       a link to the checkout's client
#   ~/.claude/settings.json           one SessionStart hook: cloud/startup.sh claude
#   ~/.codex/config.toml              one SessionStart hook: cloud/startup.sh codex
# Anything it replaces (an older checkout, another client, a changed settings file) goes
# to ~/.ail-cloud-bootstrap-backup/<YYYYMMDD-HHMMSS>/ first. A second run with the same pin
# changes nothing. bash 3.2 compatible (macOS): no mapfile, no associative arrays.
set -euo pipefail

refuse() {
  printf 'ail-cloud-bootstrap setup refused: %s\n' "$1" >&2
  exit 2
}

[[ $# -eq 2 ]] || refuse 'usage: setup.sh <source> <pin>, where the pin is a full 40-character commit hash'
source_repo="$1"
pin="$2"
[[ "$pin" =~ ^[0-9a-f]{40}$ ]] || refuse 'the pin must be a full 40-character lowercase commit hash (no branch, tag or short hash)'
[[ -n "$source_repo" && "$source_repo" != -* ]] || refuse 'the source must be a git URL or path'
[[ "${HOME-}" == /* && -d "$HOME" ]] || refuse 'HOME must be an existing absolute directory'

work="$(mktemp -d "${TMPDIR:-/tmp}/ail-cloud-setup.XXXXXX")" || refuse 'cannot create a temporary directory'
trap 'rm -rf "$work"' EXIT
staged="$work/ail-cloud-bootstrap"

git -c init.defaultBranch=main init --quiet "$staged" >/dev/null
git -C "$staged" fetch --quiet --no-tags --depth=1 -- "$source_repo" "$pin" \
  || refuse "pin $pin could not be fetched from the source"
[[ "$(git -C "$staged" cat-file -t "$pin" 2>/dev/null)" == commit ]] || refuse "pin $pin is not a commit"
git -C "$staged" -c advice.detachedHead=false checkout --quiet --detach "$pin" \
  || refuse "pin $pin could not be checked out"
[[ "$(git -C "$staged" rev-parse HEAD)" == "$pin" ]] || refuse "the checked-out commit does not equal pin $pin"
for part in bin/ail-memory hooks/session_index.py hooks/cloud_bundle.py cloud/startup.sh; do
  [[ -f "$staged/$part" && ! -L "$staged/$part" ]] || refuse "the commit at pin $pin has no $part"
done

python3 - "$HOME" "$staged" "$pin" <<'PY'
"""Plan every change first (so a settings file we cannot update safely changes nothing), then apply."""
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

home, staged, pin = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
checkout = home / "ail-cloud-bootstrap"
startup = shlex.quote(str(checkout / "cloud/startup.sh"))
# Two requests, each bounded by AIL_MEMORY_TIMEOUT_SECONDS (at most 60 s), plus the install.
HOOK_TIMEOUT = 150
CLAUDE = home / ".claude/settings.json"
CODEX = home / ".codex/config.toml"
LINK = home / ".local/bin/ail-memory"


def fail(message: str) -> None:
    sys.exit(f"ail-cloud-bootstrap setup refused: {message}; nothing was changed")


def hook_groups(groups: object, where: str) -> list:
    if not isinstance(groups, list) or not all(
            isinstance(g, dict) and isinstance(g.get("hooks", []), list)
            and all(isinstance(h, dict) for h in g.get("hooks", [])) for g in groups):
        fail(f"{where} SessionStart hooks are not a list of hook groups")
    return groups


def count(groups: list, command: str) -> int:
    return sum(h.get("command") == command for g in groups for h in g.get("hooks", []))


def plan_claude(command: str) -> str | None:
    old = CLAUDE.read_text() if CLAUDE.exists() else None
    try:
        data = json.loads(old) if old is not None else {}
    except ValueError:
        fail("~/.claude/settings.json is not valid JSON")
    if not isinstance(data, dict) or not isinstance(data.setdefault("hooks", {}), dict):
        fail("~/.claude/settings.json does not hold a hooks object")
    groups = hook_groups(data["hooks"].setdefault("SessionStart", []), "Claude")
    found = count(groups, command)
    if found == 1:
        return None
    if found > 1:
        fail("~/.claude/settings.json registers the startup hook more than once; remove the extras by hand")
    groups.append({"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT}]})
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def plan_codex(command: str) -> str | None:
    # tomllib cannot write: append text, then accept it only if it parses to exactly the
    # old config plus our one hook group, so every unrelated setting is preserved.
    text = CODEX.read_text() if CODEX.exists() else ""
    try:
        expected = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        fail("~/.codex/config.toml is not valid TOML")
    hooks = expected.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        fail("~/.codex/config.toml hooks is not a table")
    groups = hook_groups(hooks.setdefault("SessionStart", []), "Codex")
    found = count(groups, command)
    if found == 1:
        return None
    if found > 1:
        fail("~/.codex/config.toml registers the startup hook more than once; remove the extras by hand")
    entry = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT}
    groups.append({"hooks": [entry]})

    def matches(candidate: str) -> bool:
        try:
            return tomllib.loads(candidate) == expected
        except tomllib.TOMLDecodeError:
            return False

    fields = "\n".join(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in entry.items())
    appended = (text + ("\n" if text and not text.endswith("\n") else "")
                + "\n# ail-cloud-bootstrap: memory startup\n[[hooks.SessionStart]]\n\n[[hooks.SessionStart.hooks]]\n"
                + fields + "\n")
    if matches(appended):
        return appended
    # An inline SessionStart array cannot take a table header: try inserting an inline group.
    inline = "{ hooks = [{ " + ", ".join(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in entry.items()) + " }] }"
    for index, char in enumerate(text):
        if char == "]":
            for separator in ("", ", "):
                candidate = text[:index] + separator + inline + text[index:]
                if matches(candidate):
                    return candidate
    fail("cannot add the startup hook to ~/.codex/config.toml without changing other settings")


def git(*args: str, where: pathlib.Path = checkout) -> str:
    result = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def checkout_current() -> bool:
    return (checkout.is_dir() and not checkout.is_symlink() and git("rev-parse", "HEAD") == pin
            and git("status", "--porcelain", "--untracked-files=no") == "" and
            subprocess.run(["git", "-C", str(checkout), "diff", "--quiet", "HEAD"]).returncode == 0)


stamp_dir: pathlib.Path | None = None


def backup_dir() -> pathlib.Path:
    global stamp_dir
    if stamp_dir is None:
        root = home / ".ail-cloud-bootstrap-backup"
        root.mkdir(mode=0o700, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        for suffix in [""] + [f"-{n}" for n in range(2, 100)]:
            try:
                (root / (stamp + suffix)).mkdir()
            except FileExistsError:
                continue
            stamp_dir = root / (stamp + suffix)
            break
        else:
            sys.exit("ail-cloud-bootstrap setup failed: no free backup directory name")
    return stamp_dir


def move_aside(path: pathlib.Path) -> pathlib.Path:
    """Move an existing file, directory or link to the backup; never delete it."""
    saved = backup_dir() / path.relative_to(home)
    saved.parent.mkdir(parents=True, exist_ok=True)
    os.rename(path, saved)
    print(f"backed up {path.relative_to(home)}")
    return saved


def write(path: pathlib.Path, text: str) -> None:
    if path.exists():
        saved = backup_dir() / path.relative_to(home)
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, saved)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(handle, "w") as out:
        out.write(text)
    if path.exists():
        shutil.copymode(path, temporary)
    else:
        os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    print(f"updated {path.relative_to(home)}")


claude_settings = plan_claude(f"bash {startup} claude")
codex_config = plan_codex(f"bash {startup} codex")

if not checkout_current():
    # The staged tree may sit on another filesystem (TMPDIR), where a move is a copy that can
    # fail part-way. So copy it into a fresh directory inside HOME and verify it there; the old
    # checkout is touched only after that, and the switch itself is one rename. A unique
    # mkdtemp name means a directory left by a crashed earlier run is never reused.
    landing = pathlib.Path(tempfile.mkdtemp(dir=home, prefix=".ail-cloud-bootstrap-incoming-"))
    incoming = landing / "ail-cloud-bootstrap"
    try:
        shutil.move(str(staged), str(incoming))
        if git("rev-parse", "HEAD", where=incoming) != pin:
            sys.exit(f"ail-cloud-bootstrap setup failed: the copied checkout does not equal pin {pin}")
        saved = move_aside(checkout) if os.path.lexists(checkout) else None
        try:
            os.rename(incoming, checkout)
        except OSError:
            if saved is not None:
                os.rename(saved, checkout)  # put the old checkout back where the hooks expect it
            raise
    finally:
        shutil.rmtree(landing, ignore_errors=True)  # only the directory this run created
    print(f"installed ail-cloud-bootstrap at {pin}")

target = str(checkout / "bin/ail-memory")
if not (LINK.is_symlink() and os.readlink(LINK) == target):
    if os.path.lexists(LINK):
        move_aside(LINK)
    LINK.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, LINK)
    print("linked ~/.local/bin/ail-memory")

if claude_settings is not None:
    write(CLAUDE, claude_settings)
if codex_config is not None:
    write(CODEX, codex_config)
PY
