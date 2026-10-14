-- Explicit numeric-GLOBAL storage adapter only. No lifecycle listeners, no
-- file loading, and no source/item mutation.
-- A transaction records the previous value of every GLOBAL it changes, so a
-- failed campaign capture can be undone completely: an absent GLOBAL reads as
-- 0, and restoring 0 is equivalent for every script and for the runtime.
local Adapter = {}

function Adapter.new(api)
    if type(api) ~= "table" or type(api.EEex_GameState_GetGlobalInt) ~= "function" or
        type(api.EEex_GameState_SetGlobalInt) ~= "function" then return nil, "CAPABILITY" end
    local result, transaction = {}, nil
    function result:get(name) return api.EEex_GameState_GetGlobalInt(name) end
    function result:set(name, value)
        if transaction and transaction.previous[name] == nil then
            transaction.previous[name] = api.EEex_GameState_GetGlobalInt(name)
            transaction.order[#transaction.order + 1] = name
        end
        api.EEex_GameState_SetGlobalInt(name, value)
    end
    function result:begin()
        assert(transaction == nil, "STORAGE_TRANSACTION_ACTIVE")
        transaction = { previous = {}, order = {} }
    end
    function result:commit()
        assert(transaction ~= nil, "STORAGE_TRANSACTION_MISSING")
        transaction = nil
    end
    -- Restores in reverse order; a later write of the same name never hides
    -- the value from before the transaction.
    function result:rollback()
        local undo = transaction
        transaction = nil
        if not undo then return end
        for index = #undo.order, 1, -1 do
            local name = undo.order[index]
            api.EEex_GameState_SetGlobalInt(name, undo.previous[name])
        end
    end
    return result
end

return Adapter
