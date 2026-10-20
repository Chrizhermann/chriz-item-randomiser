"""Rendered campaign Lua must load in the game's LuaJIT, however large the campaign.

LuaJIT compiles at most 65536 GC constants (strings, template tables,
functions) and 65536 number constants into one function. A value whose single
table constructor exceeds that is built by several functions instead; every
other value keeps the single constructor earlier versions wrote, byte for byte.
"""
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from flir_config import campaigns
from flir_config.campaigns import _luajit_overflows, _refused_lua, _render_lua

RUNTIMES = {"LuaJIT 2.1": LuaJIT, "Lua 5.1": Lua51, "Lua 5.3": Lua53}
LOAD = """function(text)
  local chunk, failure = (loadstring or load)(text, "=fixture")
  if not chunk then return false, failure end
  return true, chunk()
end"""


def load(runtime, text: bytes):
    """The value the chunk returns, or the compiler's message."""
    ok, result = runtime.eval(LOAD)(text.decode("ascii"))
    return (result, None) if ok else (None, result)


def same(lua_value, value, lua_type) -> bool:
    """Whether a loaded Lua value holds `value` (JSON null is absent in Lua)."""
    if isinstance(value, (dict, list)):
        if lua_type(lua_value) != "table":
            return False
        expected = ({key: item for key, item in value.items() if item is not None} if isinstance(value, dict)
                    else {index: item for index, item in enumerate(value, 1) if item is not None})
        actual = dict(lua_value.items())
        return set(actual) == set(expected) and all(same(actual[key], item, lua_type)
                                                    for key, item in expected.items())
    if isinstance(value, bool) or value is None:
        return lua_value is value
    return lua_value == value and not isinstance(lua_value, bool)


def golden_value() -> dict:
    """70000 small records: one table constructor would need 140001 templates.
    The Rust port renders the same value (campaigns.rs tests) to the same bytes."""
    return {"name": "golden é", "rows": [
        {"flag": index % 3 == 0, "id": f"row-{index}", "n": index, "none": None,
         "tags": [f"t{index % 7}", index % 5]} for index in range(70000)]}


class LuaJitLimitTests(unittest.TestCase):
    """_luajit_overflows predicts exactly what LuaJIT refuses."""

    def setUp(self):
        self.luajit = LuaJIT()

    def assert_limit(self, make, fits: int):
        for count in (fits, fits + 1):
            value = make(count)
            single = _render_lua(value, single=True)
            loaded, failure = load(self.luajit, single)
            with self.subTest(make=make.__name__, count=count):
                self.assertEqual(_luajit_overflows(value), count > fits)
                if count > fits:
                    self.assertIn("main function has more than 65536 constants", failure)
                    self.assertEqual(_refused_lua(value), single)
                    self.assertNotEqual(_render_lua(value), single)
                else:
                    self.assertIsNone(failure)
                    self.assertIsNone(_refused_lua(value))
                    self.assertEqual(_render_lua(value), single)

    def test_template_tables(self):
        def templates(count):  # Each [1] = 1 table is a template; the array needs none.
            return [[1] for _ in range(count)]
        self.assert_limit(templates, 65536)

    def test_nil_entries_make_templates(self):
        def nils(count):
            return [[None] for _ in range(count)]
        self.assert_limit(nils, 65536)

    def test_string_keys_of_tables(self):
        def keyed(count):  # The object's template, a template and a key string per entry.
            return {f"k{index}": [1] for index in range(count)}
        self.assert_limit(keyed, 32767)

    def test_a_repeated_key_counts_once(self):
        def records(count):  # Two templates per record; "record" once.
            return [{"record": {"a": 1}} for _ in range(count)]
        self.assert_limit(records, 32767)

    def test_array_indexes_of_tables(self):
        def empties(count):  # Empty tables need no template; indexes above 32767 are numbers.
            return [{} for _ in range(count)]
        self.assert_limit(empties, 98303)


class SplitRenderingTests(unittest.TestCase):
    def assert_loads_everywhere(self, value, text: bytes):
        for name, runtime in RUNTIMES.items():
            lua = runtime()
            loaded, failure = load(lua, text)
            with self.subTest(runtime=name):
                self.assertIsNone(failure)
                self.assertTrue(same(loaded, value, lua.globals().type))

    def test_a_large_campaign_loads_everywhere_with_the_same_data(self):
        value = golden_value()
        text = _render_lua(value)
        self.assertTrue(_luajit_overflows(value))
        self.assertTrue(text.startswith(b'return {\n  ["\\110\\097\\109\\101"] = "\\103'))
        self.assertIn(b'["\\114\\111\\119\\115"] = (function()\nlocal t, parts = {}, {\nfunction(t)\n', text)
        self.assert_loads_everywhere(value, text)
        self.assertEqual(hashlib.sha256(text).hexdigest(),
                         "c6449d33c1b94fba6e9414f4562af3238124a8f6eb73fc05804c5abb687d5ce8")

    def test_small_parts_nest_functions_and_keep_the_data(self):
        value = {"a": [{"b": [1, 2, None, "x"], "c": {"d": True, "e": False}} for _ in range(5)],
                 "f": "é\x00\n", "g": None, "h": [], "i": {}, "j": [[[[[9007199254740991]]]]]}
        with patch.object(campaigns, "_LUAJIT_CONSTANTS", 4), patch.object(campaigns, "_LUA_PART", 8):
            text = _render_lua(value)
            self.assertEqual(_refused_lua(value), _render_lua(value, single=True))
        self.assertGreater(text.count(b"function(t)"), 5)
        self.assertTrue(text.startswith(b"return (function()\n"))
        self.assert_loads_everywhere(value, text)
        self.assertEqual(hashlib.sha256(text).hexdigest(),
                         "258544c293cfb8ce83942bd9b8fbe72fd4b6455cf5e6f402a414983868b519f3")

    def test_invalid_data_is_refused_in_split_renderings_too(self):
        with patch.object(campaigns, "_LUAJIT_CONSTANTS", 0), patch.object(campaigns, "_LUA_PART", 2):
            for value in ([0.5], [2**53], [{1: "bad key"}], ["\ud800"], [set()]):
                with self.subTest(value=repr(value)), self.assertRaises(campaigns.ProfileError):
                    _render_lua(value)
            deep = []
            for _ in range(33):
                deep = [deep]
            with self.assertRaisesRegex(campaigns.ProfileError, "nesting exceeds prototype limit 32"):
                _render_lua(deep)


if __name__ == "__main__":
    unittest.main()
