"""Offline safety checks for UI sequencing; no game or remote client is run."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import create_fixture_character as helper


class FixtureCharacterTests(unittest.TestCase):
    def test_lua_name_is_literal_utf8_in_all_supported_engines(self):
        value = '가"\\;error([[bad]])'
        helper.validate_name(value)
        for engine in (Lua51, Lua53, LuaJIT21):
            lua = engine()
            self.assertEqual(lua.eval(helper.lua_string(value)), value)
            compile_lua = lua.eval("loadstring or load")
            for label, body in helper.build_steps(value, finish_prologue=True):
                result = compile_lua(helper.render_step(label, body))
                self.assertTrue(callable(result), (engine, label, result))

    def test_invalid_names_are_rejected_before_any_actions(self):
        for value in ("", " ", "x" * 21, "test\nname", "test\0", "test\x7f"):
            with self.subTest(value=repr(value)), self.assertRaises(helper.FixtureError):
                helper.build_steps(value)

    def test_game_and_profile_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "protected"
            other = root / "other"
            game.mkdir()
            other.mkdir()
            (game / "override").mkdir()
            (game / "randomiser-campaigns").mkdir()
            for name in ("override/M_FLPR.lua", "override/M_EEexRC.lua", "randomiser-campaigns/next.lua"):
                (game / name).touch()
            with patch.object(helper, "PROTECTED_GAME", game):
                (game / "engine.lua").write_text(helper.PROFILE_TEXT, encoding="utf-8")
                self.assertEqual(helper.validate_game(game), game.resolve())
                with self.assertRaisesRegex(helper.FixtureError, "limited"):
                    helper.validate_game(other)
                (game / "engine.lua").write_text('engine_name = "EET"\n', encoding="utf-8")
                with self.assertRaisesRegex(helper.FixtureError, "private test profile"):
                    helper.validate_game(game)

    def run_fake(self, run, *, pending=None, finish_prologue=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        game = root / "game"
        (game / "override").mkdir(parents=True)
        if pending:
            (game / "override" / pending).write_text("already queued", encoding="utf-8")
        evidence = root / "evidence"
        def invoke():
            with redirect_stdout(io.StringIO()):
                return helper.drive(game, "FLPR Test", evidence, "unused-powershell",
                                    finish_prologue=finish_prologue, run=run)
        return game, evidence, invoke

    @staticmethod
    def success(argv, **kwargs):
        step = Path(argv[-2][1:]).stem
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "ok", "returnValues": [
            {"step": step, "screens": ["FLPR_RUN"], "fixture": {"committed": 2}}
        ]}), "")

    def test_pending_or_claimed_command_is_never_overwritten(self):
        for pending in ("eeex_remote_cmd.lua", "eeex_remote_cmd.lua.run"):
            calls = []
            game, evidence, invoke = self.run_fake(lambda *a, **k: calls.append(a), pending=pending)
            with self.assertRaisesRegex(helper.FixtureError, "pending"):
                invoke()
            self.assertEqual(calls, [])
            self.assertEqual((game / "override" / pending).read_text(), "already queued")
            self.assertEqual(json.loads((evidence / "status.json").read_text())["status"], "stopped")

    def test_timeout_stops_without_retry_and_preserves_pending_command(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if len(calls) == 1:
                return self.success(argv, **kwargs)
            (game / "override" / "eeex_remote_cmd.lua").write_text("pending UI action")
            return subprocess.CompletedProcess(argv, 2, '{"status":"timeout"}', "")
        game, evidence, invoke = self.run_fake(run)
        with self.assertRaisesRegex(helper.FixtureError, "No retry"):
            invoke()
        self.assertEqual(len(calls), 2)
        self.assertEqual((game / "override" / "eeex_remote_cmd.lua").read_text(), "pending UI action")
        report = json.loads((evidence / "status.json").read_text())
        self.assertEqual(report["steps"][-1]["status"], "timeout")
        self.assertFalse((evidence / "03-start-new-game.lua").exists())

    def test_client_timeout_is_recorded_without_another_call(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, 15)
        _, evidence, invoke = self.run_fake(run)
        with self.assertRaisesRegex(helper.FixtureError, "No retry"):
            invoke()
        self.assertEqual(len(calls), 1)
        detail = json.loads((evidence / "01-check-main-menu.json").read_text())
        self.assertEqual(detail["status"], "client-timeout")

    def test_invalid_and_mismatched_responses_stop_the_sequence(self):
        for response in ('not json', '[]', '{"status":"ok","returnValues":[]}',
                         '{"status":"ok","returnValues":[{"step":"another-step"}]}',
                         '{"status":"error","error":"EXPECTED_MENU:START"}'):
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, response, "")
            _, evidence, invoke = self.run_fake(run)
            with self.subTest(response=response), self.assertRaises(helper.FixtureError):
                invoke()
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads((evidence / "status.json").read_text())["status"], "stopped")

    def test_default_success_stops_before_prologue_and_does_not_claim_ready(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return self.success(argv, **kwargs)
        _, evidence, invoke = self.run_fake(run)
        report = invoke()
        self.assertEqual(len(calls), 13)
        self.assertEqual(report["status"], "awaiting-prologue-world-confirmation")
        self.assertEqual(len(list(evidence.glob("*.lua"))), 13)
        self.assertTrue(all("-NonInteractive" in call for call in calls))

    def test_optional_prologue_still_requires_separate_loot_verification(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return self.success(argv, **kwargs)
        _, _, invoke = self.run_fake(run, finish_prologue=True)
        report = invoke()
        self.assertEqual(len(calls), 15)
        self.assertEqual(report["status"], "awaiting-loot-confirmation")

    def test_remote_client_is_beside_the_main_checkout_from_any_worktree(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        main, worktree = root / "repository", root / "elsewhere" / "worktree"
        subprocess.run(["git", "init", "-q", str(main)], check=True)
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", "--orphan", "-b", "other",
                        str(worktree)], check=True)
        expected = root / "eeex-remote-console" / "tools" / "eeex-remote.ps1"
        self.assertEqual(helper.sibling_remote_client(main), expected)
        self.assertEqual(helper.sibling_remote_client(worktree), expected)


if __name__ == "__main__":
    unittest.main()
