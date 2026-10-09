"""The run contract the SDLC gate depends on: if this breaks, `sdlc gate` cannot be trusted."""

import json
import os
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
REQUIRED_KEYS = {"check", "static", "acceptance", "acceptance_dir", "default_branch"}


def load_contract() -> dict[str, object]:
    data = json.loads((REPO / ".sdlc.json").read_text())
    assert isinstance(data, dict)
    return data


class RepoContract(unittest.TestCase):
    def test_sdlc_json_names_every_required_command(self) -> None:
        # Catches a missing or renamed key that would make the gate skip a stage.
        # Misses: whether each command actually passes (scripts/check itself proves that).
        self.assertTrue(REQUIRED_KEYS <= load_contract().keys())

    def test_the_check_script_is_the_executable_named_in_the_contract(self) -> None:
        # Catches a gate that silently stops running because the file lost its executable bit.
        check = REPO / str(load_contract()["check"])
        self.assertTrue(check.is_file())
        self.assertTrue(os.access(check, os.X_OK))

    def test_acceptance_folder_exists_and_is_collected_by_the_check(self) -> None:
        # Catches locked tests that would be written where the gate never looks.
        acceptance = REPO / str(load_contract()["acceptance_dir"])
        self.assertTrue((acceptance / "__init__.py").is_file())

    def test_full_lane_paths_are_globs_of_strings(self) -> None:
        # Catches a malformed list, which the gate treats as a hard failure.
        paths = load_contract().get("full_lane_paths", [])
        self.assertIsInstance(paths, list)
        assert isinstance(paths, list)
        self.assertTrue(all(isinstance(p, str) and p for p in paths))

    def test_stop_hook_is_executable(self) -> None:
        # Catches an agent being allowed to end a turn with a red check.
        self.assertTrue(os.access(REPO / "scripts" / "sdlc-stop-hook", os.X_OK))


if __name__ == "__main__":
    unittest.main()
