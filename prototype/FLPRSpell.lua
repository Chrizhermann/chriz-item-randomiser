-- SPIN621's reviewed Create Item effect calls FLPRKEY on its actual recipient.
-- EEex_Opcode_Patch.lua documents the (CGameEffect, CGameSprite) signature.
-- No listeners or item transport live here; Host owns the saved campaign flags.
local Spell = {}
local SELECTED, CONSUMED, EVENT, RECIPIENT = 'FLPR_QKEY', 'FLPR_XKEY', 'FLPR_EKEY', 'FLPR_RKEY'

function Spell.install(api)
    assert(type(api)=='table','CAMPAIGN_SPELL_API_INVALID')
    for _,name in ipairs({'EEex_GameState_GetGlobalInt','EEex_GameState_SetGlobalInt',
        'EEex_Sprite_SetLocalInt','EEex_Sprite_GetLocalInt','EEex_GameObject_ApplyEffect'}) do
        assert(type(api[name])=='function',name..'_MISSING')
    end
    local service={last_error=nil}

    local function apply_original(effect,sprite)
        -- The reviewed SPL supplies timing9, no save, and probability100/0.
        -- The 402 has already passed the engine's application checks. noSave
        -- avoids applying immunity/resistance checks a second time to this
        -- resolved recipient. ApplyEffect submits the effect; it returns no
        -- inventory receipt, so this callback does not claim item materialization.
        api.EEex_GameObject_ApplyEffect(sprite,{
            effectID=122,targetType=effect.m_targetType,spellLevel=0,
            effectAmount=effect.m_effectAmount,dwFlags=effect.m_dWFlags,
            durationType=9,duration=0,probabilityUpper=100,probabilityLower=0,
            res='ring41',numDice=0,diceSize=0,savingThrow=0,saveMod=0,special=0,
            sourceID=effect.m_sourceId,sourceTarget=effect.m_sourceTarget,
            sourceX=effect.m_source.x,sourceY=effect.m_source.y,
            targetX=effect.m_target.x,targetY=effect.m_target.y,
            m_flags=effect.m_flags,m_sourceType=effect.m_sourceType,
            m_sourceRes=effect.m_sourceRes:get(),m_sourceFlags=effect.m_sourceFlags,
            m_projectileType=effect.m_projectileType,m_slotNum=effect.m_slotNum,
            m_casterLevel=effect.m_casterLevel,m_secondaryType=effect.m_secondaryType,
            m_school=effect.m_school,noSave=1,immediateResolve=1,
        })
    end

    local function invoke(effect,sprite)
        assert(effect and sprite,'CAMPAIGN_SPELL_RECIPIENT_MISSING')
        if effect.m_done==true or effect.m_done==1 then return true end
        -- The original effect has permanent timing9. Match EEex's own one-shot
        -- opcode callback pattern (EEex_Opcode.lua, OnOp214ApplyEffect), including
        -- before reentrant effect-list resolution. Do not leave a repeat grant.
        effect.m_done=true
        if service.last_error then return nil,service.last_error end
        local selected=api.EEex_GameState_GetGlobalInt(SELECTED)==1
        if not selected then apply_original(effect,sprite) end
        -- A saved local identifies the actual recipient across reload/reordering.
        -- Later independent casts must never redirect the first campaign reward.
        if api.EEex_GameState_GetGlobalInt(EVENT)~=1 then
            api.EEex_Sprite_SetLocalInt(sprite,RECIPIENT,1)
            assert(api.EEex_Sprite_GetLocalInt(sprite,RECIPIENT)==1,
                'CAMPAIGN_SPELL_RECIPIENT_MARK_FAILED')
        end
        if selected then api.EEex_GameState_SetGlobalInt(CONSUMED,1) end
        api.EEex_GameState_SetGlobalInt(EVENT,1)
        return true
    end

    service.callback=function(effect,sprite)
        local ok,result,reason=pcall(invoke,effect,sprite)
        if not ok then
            service.last_error=tostring(result)
            if type(api.Infinity_Log)=='function' then
                pcall(api.Infinity_Log,'FLPR spell reward: '..service.last_error)
            end
            return nil,service.last_error
        end
        return result,reason
    end
    api.FLPRKEY=service.callback
    return service
end

return Spell
