-- Installed by cir.tp2, which points the user interface's
-- startEngine:OnNewGameButtonClick(), startEngine:OnImportCharacterButtonClick()
-- and createCharScreen:AcceptCharacter() calls here, in UI.MENU and in other
-- menu and Lua files. Loaded by the engine itself, also without EEex.
-- Without an applied configuration (randomiser-campaigns/next.lua) the calls
-- go straight to the engine. With one, New Game and character import run
-- through the campaign runtime (M_FLPR.lua, which EEex loads). Whenever a new
-- adventure cannot be randomised, the player is told why and chooses between
-- starting it without randomised items and cancelling; it never starts
-- unrandomised silently.
local NEXT = "randomiser-campaigns/next.lua"

-- English fallbacks (languages/english/in-game.tra @1700-@1706). The installed
-- M_FLPRTXT.lua maps the same keys to the strings the installer added to the
-- game text in the language chosen at installation.
local FALLBACK = {
    not_running = "Chriz Item Randomiser is set up but not running. "
        .. "Start the game with InfinityLoader.exe to randomise this adventure.",
    unsupported = "Chriz Item Randomiser does not support this game version yet. "
        .. "Details: randomiser-campaigns\\last-error.txt",
    other_campaign = "Chriz Item Randomiser's settings have nothing to randomise in this campaign.",
    setup_failed = "Chriz Item Randomiser could not randomise this adventure. "
        .. "Details: randomiser-campaigns\\last-error.txt",
    start_plain = "Start without randomiser",
    ready = "Chriz Item Randomiser: this adventure's items are randomised.",
    stopped = "Chriz Item Randomiser stopped working in this adventure. Items it has not placed yet may be "
        .. "missing. Details: randomiser-campaigns\\last-error.txt",
}

function FLPRUI_Text(key)
    local refs = FLPR_TEXT
    local ref = type(refs) == "table" and refs[key]
    if type(ref) == "number" and ref >= 0 and type(Infinity_FetchString) == "function" then
        local text = Infinity_FetchString(ref)
        if type(text) == "string" and text ~= "" then return text end
    end
    return FALLBACK[key] or key
end

-- New Game and Import close their start menu before they call here (the stock
-- START_SP pops itself), so a popup they show stands alone. Its Cancel opens
-- the start screen's menus again as the engine does when the start screen
-- returns: START, whose onOpen opens START_SP, START_MP or START_MAIN for the
-- engine state (BG:EE and EET pop START there, BG2:EE keeps it below).
local START_MENUS = { "START_SP", "START_MP", "START_MAIN" }
local asking_from_start = false

local function reopen_start()
    for _, name in ipairs(START_MENUS) do
        if Infinity_IsMenuOnStack(name) then return end
    end
    if Infinity_IsMenuOnStack("START") then Infinity_PopMenu("START") end
    Infinity_PushMenu("START")
end

local function start_cancel()
    if asking_from_start and type(Infinity_IsMenuOnStack) == "function" and
        type(Infinity_PushMenu) == "function" and type(Infinity_PopMenu) == "function" then
        return reopen_start
    end
end

-- Explains why this adventure cannot be randomised. The engine's own popup
-- offers "Start without randomiser" (start_plain) and Cancel, which returns to
-- the start screen's menus or, in character creation, to character creation.
function FLPRUI_Ask(key, start_plain)
    local text = FLPRUI_Text(key)
    if type(popup2Button) ~= "function" or type(uiStrings) ~= "table" then
        -- A UI without the stock popup: refuse rather than start unrandomised.
        error(text, 0)
    end
    uiStrings.FLPR_POPUP = text
    uiStrings.FLPR_START_PLAIN = FLPRUI_Text("start_plain")
    popup2Button("FLPR_POPUP", "FLPR_START_PLAIN", start_plain, "CANCEL_BUTTON", start_cancel())
end

-- Runs a start button's call; popups it shows (also the runtime's) get the
-- start screen's Cancel.
local function from_start(call, ...)
    asking_from_start = true
    local ok, result = pcall(call, ...)
    asking_from_start = false
    if not ok then error(result, 0) end
    return result
end

local function configured()
    if type(io) == "table" and type(io.open) == "function" then
        local file = io.open(NEXT, "rb")
        if not file then return false end
        file:close()
        return true
    end
    -- The engine's own Lua has no io library. A configuration that exists but
    -- does not compile still counts, so the runtime reports it.
    if type(loadfile) ~= "function" then return false end
    local chunk, failure = loadfile(NEXT)
    return chunk ~= nil or not tostring(failure):find("cannot open", 1, true)
end

local function runtime(name)
    if type(FLPR) == "table" and type(FLPR[name]) == "function" then return FLPR[name] end
end

-- Mods built on DavidW's SFO library (SCS, IWDification) can send character
-- acceptance through their dispatcher, dwSystemCall, whose default function
-- (m_dw_osd.lua) calls the engine with one argument. The installer points
-- that call here; such a mod installed later writes the file anew, so a new
-- adventure points the default here again.
local SFO_ACCEPT = "createCharScreen:AcceptCharacter"

local function sfo_accept(difficulty)
    return FLPRUI_AcceptCharacter(difficulty)
end

local function hook_sfo_dispatcher()
    local defaults = rawget(_G, "dwSystemCallDefault")
    if type(defaults) == "table" and type(defaults[SFO_ACCEPT]) == "function" then
        defaults[SFO_ACCEPT] = sfo_accept
    end
end

function FLPRUI_NewGame(...)
    if not configured() then return startEngine:OnNewGameButtonClick(...) end
    hook_sfo_dispatcher()
    local new_game = runtime("NewGame")
    if not new_game then
        local arguments = { ... }
        return from_start(FLPRUI_Ask, "not_running", function()
            startEngine:OnNewGameButtonClick((unpack or table.unpack)(arguments))
        end)
    end
    return from_start(new_game, ...)
end

function FLPRUI_ImportCharacter(...)
    if not configured() then return startEngine:OnImportCharacterButtonClick(...) end
    hook_sfo_dispatcher()
    local import = runtime("ImportCharacter")
    if not import then
        local arguments = { ... }
        return from_start(FLPRUI_Ask, "not_running", function()
            startEngine:OnImportCharacterButtonClick((unpack or table.unpack)(arguments))
        end)
    end
    return from_start(import, ...)
end

function FLPRUI_AcceptCharacter(...)
    local accept = configured() and runtime("AcceptCharacter")
    if accept then return accept(...) end
    return createCharScreen:AcceptCharacter(...)
end
