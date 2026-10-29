"""The check configuration the verification command depends on: if this breaks, the check cannot be trusted."""

import json
import os
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
REQUIRED_KEYS = {"check", "static", "default_branch"}


def load_contract() -> dict[str, object]:
    data = json.loads((REPO / ".check.json").read_text())
    assert isinstance(data, dict)
    return data


class RepoContract(unittest.TestCase):
    def test_check_json_names_every_required_command(self) -> None:
        # Catches a missing or renamed key that would make the gate skip a stage.
        # Misses: whether each command actually passes (scripts/check itself proves that).
        self.assertTrue(REQUIRED_KEYS <= load_contract().keys())

    def test_the_check_script_is_the_executable_named_in_the_contract(self) -> None:
        # Catches a gate that silently stops running because the file lost its executable bit.
        check = REPO / str(load_contract()["check"])
        self.assertTrue(check.is_file())
        self.assertTrue(os.access(check, os.X_OK))

    def test_acceptance_folder_exists_and_is_collected_by_the_check(self) -> None:
        # Catches kept acceptance tests sitting where the check never looks.
        self.assertTrue((REPO / "tests" / "acceptance" / "__init__.py").is_file())

    def test_v1_process_files_are_gone(self) -> None:
        # Catches a stale v1 contract or stop-hook registration coming back.
        for stale in (".sdlc.json", ".claude/settings.json", ".codex/config.toml"):
            self.assertFalse((REPO / stale).exists(), stale)

    def test_stop_hook_is_executable(self) -> None:
        # Catches an agent being allowed to end a turn with a red check.
        self.assertTrue(os.access(REPO / "scripts" / "sdlc-stop-hook", os.X_OK))


if __name__ == "__main__":
    unittest.main()
