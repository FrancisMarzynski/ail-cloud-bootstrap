"""Cloud session start, step 1: read the memory index through the client, or block.

Prints one SessionStart hook object whose context is the whole index. Any failure
(missing, crashing or silent client, refusal, timeout, empty or malformed index)
exits non-zero with one bounded `ail-cloud:` reason line on stderr. The client's own
stderr is never forwarded, so nothing it might hold can leak through this hook.
"""

import json
import pathlib
import subprocess
import sys
from typing import NoReturn

PREFIX = "Memory index. Read memory.kinds and the table comments before writing."
INDEX_REQUEST = json.dumps({"version": 1, "sql": "select memory.session_index()", "params": []})
CLIENT_OUTPUT_LIMIT = 1048576 * 2  # the client's response limit, plus room for re-encoding
OUTCOMES = ("not_executed", "rolled_back", "unknown")


def block(reason: str) -> NoReturn:
    print(f"ail-cloud: memory index check failed ({reason})", file=sys.stderr)
    sys.exit(2)


def client_outcome(stderr: str) -> str:
    """The client's reported outcome when it is one of the known words, else `unknown`."""
    try:
        outcome = json.loads(stderr)["error"]["outcome"]
    except (ValueError, TypeError, KeyError):
        return "unknown"
    return outcome if outcome in OUTCOMES else "unknown"


def read_index() -> str:
    client = pathlib.Path(__file__).resolve().parents[1] / "bin/ail-memory"
    try:
        # The client bounds each request to at most 60 s; this only catches a hung client.
        result = subprocess.run([str(client)], input=INDEX_REQUEST, text=True, capture_output=True, timeout=65)
    except (OSError, subprocess.TimeoutExpired):
        block("client missing or not responding")
    if result.returncode:
        block(f"client outcome {client_outcome(result.stderr)}")
    if len(result.stdout.encode()) > CLIENT_OUTPUT_LIMIT:
        block("client output too large")
    try:
        response = json.loads(result.stdout)
    except ValueError:
        block("client output is not JSON")
    if (not isinstance(response, dict) or response.get("version") != 1
            or response.get("columns") != ["session_index"]
            or not isinstance(response.get("rows"), list) or len(response["rows"]) != 1
            or not isinstance(response["rows"][0], list) or len(response["rows"][0]) != 1):
        block("unexpected index shape")
    index = response["rows"][0][0]
    if not isinstance(index, str) or not index.strip():
        block("empty index")
    return index


if __name__ == "__main__":
    context = PREFIX + "\n" + read_index()
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}},
                     ensure_ascii=False))
