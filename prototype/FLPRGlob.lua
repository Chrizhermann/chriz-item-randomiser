-- Explicit numeric-GLOBAL storage adapter only. No lifecycle listeners, no
-- file loading, and no source/item mutation. Not installed by the TP2.
local Adapter = {}

function Adapter.new(api)
    if type(api) ~= "table" or type(api.EEex_GameState_GetGlobalInt) ~= "function" or
        type(api.EEex_GameState_SetGlobalInt) ~= "function" then return nil, "CAPABILITY" end
    local result = {}
    function result:get(name) return api.EEex_GameState_GetGlobalInt(name) end
    function result:set(name, value) api.EEex_GameState_SetGlobalInt(name, value) end
    return result
end

return Adapter
