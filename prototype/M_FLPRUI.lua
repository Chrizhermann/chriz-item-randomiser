-- Installed by randomiser.tp2 component 1500, which points UI.MENU's
-- startEngine:OnNewGameButtonClick() and createCharScreen:AcceptCharacter()
-- calls here. Loaded by the engine itself, also without EEex.
-- Without an applied configuration (flpr-fixture/next.lua) both calls go
-- straight to the engine. With one, New Game runs through the campaign runtime
-- (M_FLPR.lua, which EEex loads) and refuses to start when it is not running.
local NEXT = "flpr-fixture/next.lua"

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

function FLPRUI_NewGame(...)
    if not configured() then return startEngine:OnNewGameButtonClick(...) end
    local new_game = runtime("NewGame")
    if not new_game then
        error("Item Randomiser: a campaign configuration is applied, but the campaign runtime "
            .. "is not running. Start the game with EEex (InfinityLoader.exe).", 0)
    end
    return new_game(...)
end

function FLPRUI_AcceptCharacter(...)
    local accept = configured() and runtime("AcceptCharacter")
    if accept then return accept(...) end
    return createCharScreen:AcceptCharacter(...)
end
