"""FLPRSite.lua's hook locations: the table, the runtime scan and the checker.

Game executables are not distributed: `python tools/engine_patterns.py
<Baldur.exe>` checks a real one with the same patterns. Here the table is
checked for consistency, FLPRSite.lua's own scan runs on a small synthetic
executable in each Lua runtime, and so does the offline checker.
"""
import contextlib
import io
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests")]
import engine_patterns  # noqa: E402
from engine_sites import SITE, SITES_266  # noqa: E402

SPECS = engine_patterns.parse_specs(SITE)
TEXT_VA, TEXT_RAW, DATA_RAW = 0x1000, 0x400, 0x600


def nibbles(text):
    return [None if text[n:n+2] == "??" else int(text[n:n+2], 16) for n in range(0, len(text), 2)]


def synthetic_pe(code, data=b""):
    """A minimal PE32+ file: headers, .text (code) and .data, 0x200 bytes each."""
    pe = 0x40
    header = bytearray(TEXT_RAW)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, pe)
    header[pe:pe+4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", header, pe+4, 0x8664, 2, 0x5EED, 0, 0, 0xF0, 0x22)
    struct.pack_into("<H", header, pe+24, 0x20B)
    for index, (name, va, raw) in enumerate(((b".text", TEXT_VA, TEXT_RAW), (b".data", 0x2000, DATA_RAW))):
        at = pe + 24 + 0xF0 + index * 40
        header[at:at+8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", header, at+8, 0x200, va, 0x200, raw)
    return bytes(header) + bytes(code).ljust(0x200, b"\0") + bytes(data).ljust(0x200, b"\0")


class LocationTableTests(unittest.TestCase):
    def test_each_pattern_has_a_fixed_anchor_and_agrees_with_its_hook_bytes(self):
        self.assertEqual(len({spec.key for spec in SPECS}), len(SPECS))
        for spec in SPECS:
            with self.subTest(key=spec.key):
                pattern = nibbles(spec.pattern)
                self.assertLess(spec.at, len(pattern))
                longest = max(len(run) for run in re.split(r"(?:\?\?)+", spec.pattern)) // 2
                self.assertGreaterEqual(longest, 8)
                for index, byte in enumerate(nibbles(spec.expected)):
                    if spec.at + index < len(pattern) and None not in (byte, pattern[spec.at+index]):
                        self.assertEqual(byte, pattern[spec.at+index], index)

    def test_harness_code_is_what_the_runtime_expects(self):
        self.assertEqual({spec.key for spec in SPECS}, set(SITES_266))
        for spec in SPECS:
            code = SITES_266[spec.key][1]
            with self.subTest(key=spec.key):
                self.assertEqual(len(code), len(spec.expected))
                self.assertTrue(engine_patterns.matches(bytes.fromhex(code), spec.expected))

    def test_runtime_modules_use_every_location_and_only_known_ones(self):
        used = set()
        for name in ("FLPRGat.lua", "FLPRShop.lua", "FLPRCur.lua"):
            text = (ROOT / "prototype" / name).read_text(encoding="utf-8")
            used |= set(re.findall(r"key='(\w+)'", text))
            used |= set(re.findall(r"Site\.locate\(api,'(\w+)'", text))
            used |= set(re.findall(r"calls\((?:site\.key|'\w+'),'(\w+)'\)", text))
            used |= set(re.findall(r"calls\('(\w+)',", text))
            for sites in re.findall(r"local SITES=\{([^}]*)\}", text):
                used |= set(re.findall(r"'(\w+)'", sites))
        self.assertEqual(used, {spec.key for spec in SPECS})


SCAN_SPECS = """Site.SPECS = {
    -- once, with wildcards
    one={at=2,pattern='AABB????CCDD',
        bytes='??02cc'},
    twice={at=0,pattern='7766554433221100',
        bytes='77'},
    missing={at=0,pattern='DEADBEEF00112233',
        bytes='de'},
    -- the anchor is not the first byte
    lead={at=1,pattern='????3344556677889900',
        bytes='88'},
    -- crosses the end of the code section / lies in another section
    edge={at=0,pattern='EE??FF0102030405060708',
        bytes='ee'},
}
"""


def scan_code():
    code = bytearray(0x200)
    code[0x10:0x16] = bytes.fromhex("aabb0102ccdd")
    for at in (0x40, 0x80):
        code[at:at+8] = bytes.fromhex("7766554433221100")
    code[0x60:0x6A] = bytes.fromhex("99883344556677889900")
    code[0x1FE:0x200] = bytes.fromhex("ee00")
    data = bytes.fromhex("ff0102030405060708") + bytes.fromhex("ee00ff0102030405060708")
    return code, data


class CheckerTests(unittest.TestCase):
    def test_reports_each_location(self):
        specs = engine_patterns.parse_specs(SCAN_SPECS)
        image = engine_patterns.Executable(synthetic_pe(*scan_code()))
        self.assertEqual(engine_patterns.check(image, specs), [
            "ok   one: 0x1012", "FAIL twice: 2 matches", "FAIL missing: 0 matches",
            "ok   lead: 0x1061", "FAIL edge: 0 matches"])
        changed = engine_patterns.parse_specs(SCAN_SPECS.replace("bytes='??02cc'", "bytes='0103'"))
        self.assertEqual(engine_patterns.check(image, changed)[0],
                         "FAIL one: at 0x1012 the bytes differ (0102)")

    def test_rejects_unreadable_input(self):
        for text in ("Site.SPECS = {\n    broken\n}\n", "Site.SPECS = {\n}\n",
                     "Site.SPECS = {\n    one={at=0,pattern='ABC',\n        bytes='ab'},\n}\n"):
            with self.subTest(text=text), self.assertRaises(engine_patterns.PatternError):
                engine_patterns.parse_specs(text)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Baldur.exe"
            path.write_bytes(b"not an executable")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(engine_patterns.main([str(path)]), 1)
                self.assertEqual(engine_patterns.main([]), 1)
            self.assertIn("FAIL not a PE executable", output.getvalue())


LUA_CASES = r"""
local Site,specs,exe,TEXT_VA,TEXT_RAW=...
local cases={}
local BASE=0x40000000
Site.SPECS=specs
-- The mapped image: headers, then .text at its virtual address; changes on top.
local function fixture(files)
    local f={files=files or {['Baldur.exe']=exe},reads={},memory={}}
    Site.read_file=function(name)
        f.reads[#f.reads+1]=name
        return f.files[name]
    end
    f.api={EEex_GetImageBase=function() return BASE end,
        EEex_ReadU8=function(address)
            if f.memory[address] then return f.memory[address] end
            local at=address-BASE
            if at<TEXT_RAW then return exe:byte(at+1) end
            if at>=TEXT_VA and at<TEXT_VA+0x200 then return exe:byte(TEXT_RAW+at-TEXT_VA+1) end
            return 0
        end,
        EEex_Read32=function(address)
            local value=0
            for at=3,0,-1 do value=value*256+f.api.EEex_ReadU8(address+at) end
            return value>=2147483648 and value-4294967296 or value
        end}
    return f
end
local function reason(f,key) return select(2,Site.find(f.api,key)) end
local function fails(expected,action)
    local ok,message=pcall(action)
    assert(not ok and message==expected,tostring(message))
end
function cases.each_location_must_occur_once_in_the_code_section()
    local f=fixture()
    assert(Site.find(f.api,'one')==BASE+TEXT_VA+0x12)
    assert(Site.find(f.api,'lead')==BASE+TEXT_VA+0x61)
    assert(reason(f,'twice')=='twice:ambiguous:2' and reason(f,'missing')=='missing:not-found')
    assert(reason(f,'edge')=='edge:not-found' and reason(f,'other')=='unknown-site:other')
    -- One read per session; the result is shared through the API table.
    assert(#f.reads==1 and f.api.FLPR_ENGINE_SITES.file=='Baldur.exe')
    f.memory[BASE+TEXT_VA+0x13]=0x99
    assert(reason(f,'one')=='one:differs-at:1')
    fails('SHOP_CODE_MISMATCH:one:differs-at:1',function() Site.locate(f.api,'one','SHOP') end)
end
function cases.the_executable_must_be_the_running_one()
    local other=exe:sub(1,0x48)..'\255'..exe:sub(0x4A)
    local f=fixture({['Baldur.exe']=other,['SiegeOfDragonspear.exe']=exe})
    assert(Site.find(f.api,'one')==BASE+TEXT_VA+0x12)
    assert(f.api.FLPR_ENGINE_SITES.file=='SiegeOfDragonspear.exe')
    f=fixture({['Baldur.exe']=other})
    assert(reason(f,'one')=='engine:not-the-running-executable:Baldur.exe')
    f=fixture({['Baldur.exe']='MZ not really'})
    assert(reason(f,'one')=='engine:not-the-running-executable:Baldur.exe')
    f=fixture({})
    assert(reason(f,'one')=='engine:executable-not-found')
    assert(#f.reads==#Site.EXECUTABLES and reason(f,'lead')=='engine:executable-not-found' and
        #f.reads==#Site.EXECUTABLES)
    f=fixture(); f.api.EEex_GetImageBase=nil
    assert(reason(f,'one')=='engine:image-base-unknown' and #f.reads==0)
end
function cases.globals_and_relative_targets()
    local f=fixture()
    f.api.EEex_TryLabel=function(label) if label=='g_pBaldurChitin' then return 0x7650A0 end end
    assert(Site.global(f.api,'g_pBaldurChitin','SHOP')==0x7650A0)
    fails('SHOP_CODE_MISMATCH:g_pOther:not-found',function() Site.global(f.api,'g_pOther','SHOP') end)
    for at,byte in ipairs({0xE8,0xF0,0xFF,0xFF,0xFF}) do f.memory[0x2000+at-1]=byte end
    assert(Site.call_target(f.api,0x2000)==0x1FF5)
    for at,byte in ipairs({0x48,0x63,0x35,0x7B,0x59,0x32,0x00}) do f.memory[0x3000+at-1]=byte end
    assert(Site.rip_target(f.api,0x3000,7)==0x3000+7+0x32597B)
end
function cases.layout_stops_only_on_a_known_different_offset()
    local f=fixture()
    Site.check_layout(f.api,'shop','SHOP')
    local offsets={['CStore.m_lInventory']=0xA0,['CItem.cResRef']=0x10}
    f.api.EEex_OffsetOf=function(name) return offsets[name] or error('unknown '..name) end
    Site.check_layout(f.api,'shop','SHOP')
    offsets['CItem.cResRef']=0x18
    fails('SHOP_CODE_MISMATCH:layout:CItem.cResRef:24',function() Site.check_layout(f.api,'shop','SHOP') end)
end
return cases
"""


class RuntimeScanTests(unittest.TestCase):
    def test_lua_cases(self):
        exe = synthetic_pe(*scan_code())
        for runtime in (Lua51, Lua53, LuaJIT):
            lua = runtime(unpack_returned_tuples=True)
            site = lua.execute(SITE)
            specs = lua.execute(SCAN_SPECS.replace("Site.SPECS =", "return"))
            cases = lua.execute(LUA_CASES, site, specs, exe, TEXT_VA, TEXT_RAW)
            for name in sorted(cases.keys()):
                with self.subTest(runtime=runtime.__module__, case=name):
                    cases[name]()


if __name__ == "__main__":
    unittest.main()
