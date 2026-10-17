"""BG2:EE 2.6.6.0 engine code at FLPRSite.lua's hook locations, for harnesses.

FLPRSite.lua finds each hook location by scanning the running Baldur.exe once
per session and keeps the result in api.FLPR_ENGINE_SITES. The harnesses model
one real build instead: each location is at its BG2:EE 2.6.6.0 offset and that
build's bytes are there, so call targets and RIP-relative globals agree as they
do in the game. test_engine_sites.py checks this table against FLPRSite.lua and
runs the scan itself on synthetic executables.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = (ROOT / "prototype" / "FLPRSite.lua").read_text(encoding="utf-8")

# Site.SPECS key -> (offset from the image base, bytes there) in BG2:EE 2.6.6.0.
SITES_266 = {
    "gate_enter": (0x292A84, "48894df8488d4dc8488945a8410fb6f944884c2460"),
    "gate_capture": (0x292F45, "8bd6413974242c0f86c40400000f1f400066660f1f84"),
    "gate_item": (0x190270, "418bc7488d0c80488b45884c8d2c88498bd5488d4df8"),
    "gate_area_enter": (0x18B8AC, "4533e444884c24604489642468450fb6e94c8bfa4c8bf1"),
    "gate_area_exit": (0x18DF94, "488b9c2470020000418bc4488b8d000100004833cc"),
    "gate_container_call": (0x18D9C9, "e8322400004885c00f84e90000004c8b00418bd4488bc8"),
    "gate_container_entry": (0x18FE00, "48895c24205556574154415541564157488d6c24804881ec80010000"),
    "gate_container_skip": (0x1906FC, "418b464441ffc74103464041bc0000000044897c2450"
                                      "443bf80f8255fbffff4c8b6d98410f"),
    "gate_actor1": (0x18C448, "e8d34d1b00498db580000000e93d010000"),
    "gate_actor2": (0x18C591, "e88a4c1b00488bf84885c00f8444010000"),
    "gate_store": (0x250765, "0f1047080f114308"),
    "gate_saved_store": (0x405295, "c6435001498bcf"),
    "gate_store_done": (0x250967, "4c8b742448"),
    "gate_template": (0x341220, "48895c24185556574154415541564157"),
    "cur_demand1": (0x3F63A4, "488b41404883c420"),
    "cur_demand2": (0x3F63D3, "488b43404883c420"),
    "cur_registry_count": (0x402306, "4863357b593200"),
    "cur_registry_array": (0x402330, "488b1d49593200"),
    "shop_add_item_call": (0x28CA79, "e8e22cfcff"),
    "shop_after_call": (0x28CA7E, "488d8c2470010000"),
    "shop_price": (0x24FB9A, "488d4f108bf0"),
    "shop_compress": (0x24F860, "488bd3488d8ea0000000"),
    "shop_merge": (0x24F487, "0f8582000000"),
    "shop_select": (0x2E7E00, "6639421c490f44f9"),
    "shop_price_call": (0x2E7C40, "e82b7ff6ff89431c"),
}
CHITIN_266 = 0x6650A0  # EEex label g_pBaldurChitin
# What FLPRGat.lua derives from the locations for FLPRGateAsm.lua.
GATE_TARGETS_266 = {"item_resume": 0x19027F, "container_return": 0x18D9CE,
                    "container_skip": 0x1906FC}


def _lua_sites():
    rows = "".join(f"    {key}={{0x{offset:X},'{code}'}},\n"
                   for key, (offset, code) in SITES_266.items())
    return "{\n" + rows + "}"


# engine_image(api, base, poke) writes each location's bytes with
# poke(address, byte) and gives api what the session scan would have found.
ENGINE_LUA = """
local ENGINE_SITES=""" + _lua_sites() + """
local function engine_image(api,base,poke)
    local offsets={}
    for key,site in pairs(ENGINE_SITES) do
        offsets[key]={site[1]}
        local at=0
        for pair in site[2]:gmatch('%x%x') do
            poke(base+site[1]+at,tonumber(pair,16)); at=at+1
        end
    end
    function api.EEex_GetImageBase() return base end
    api.FLPR_ENGINE_SITES={base=base,file='Baldur.exe',offsets=offsets}
    function api.EEex_TryLabel(label)
        if label=='g_pBaldurChitin' then return base+""" + f"0x{CHITIN_266:X}" + """ end
    end
end
"""


def site(lua):
    """FLPRSite.lua in this Lua runtime."""
    return lua.execute(SITE)


def load(lua, source):
    """Run a runtime module chunk with FLPRSite.lua as its argument, as M_FLPR does."""
    return lua.execute(source, site(lua))


def gate_targets(lua, image):
    """FLPRGateAsm.lua's targets for an image loaded at image."""
    return lua.table_from({name: image + offset for name, offset in GATE_TARGETS_266.items()})
