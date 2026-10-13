-- Installed by randomiser.tp2 component 1500 or staged by the isolated fixture installer.
if not EEex_Active then return end
FLPR = FLPR or {}
local root = FLPR
if root.Held then root.Held:destroy(); root.Held = nil end
if root.Host then root.Host:destroy() end
root.Disabled, root.Host, root.NewGameIntent = "INITIALIZING", nil, nil
root.QA = nil
root.SeedSerial = root.SeedSerial or 0

-- Remote commands are evaluated during drawing. Save/load transitions can
-- release bitmap userdata still referenced by that drawing pass (live crash
-- evidence in docs/native-save-render-crash.md). Only the finite QA runner may
-- queue normal button events here; arbitrary remote calls remain blocked.
-- Gate centrally so a background overlay cannot claim the command either.
if EEexRemote and type(EEexRemote.Poll) == "function" then
    root.RemotePollOriginal = root.RemotePollOriginal or EEexRemote.Poll
    EEexRemote.Poll = function(...)
        if root.QA then root.QA:poll() end
        for _, name in ipairs({ "SAVE", "SAVE_NEWSAVE", "LOAD" }) do
            if Infinity_IsMenuOnStack(name) then return "" end
        end
        return root.RemotePollOriginal(...)
    end
end

local function module(path)
    return assert(loadfile(path))()
end

local ok, failure = pcall(function()
    local Core = module("override/FLPRCore.lua")
    local Globals = module("override/FLPRGlob.lua")
    local Native = module("override/FLPRNat.lua")
    local Host = module("override/FLPRHost.lua")
    local QA = module("override/FLPRQA.lua")
    if not root.Spell then
        local spell=loadfile('override/FLPRSpell.lua')
        if spell then root.Spell=spell().install(_G) end
    end
    local storage = assert(Globals.new(_G))
    local function resolve(revision)
        if type(revision) ~= "string" or #revision ~= 64 or revision:find("[^0-9a-f]") then return nil end
        local fn = loadfile("flpr-fixture/campaign-store/revisions/" .. revision .. ".lua")
        if not fn then return nil end
        local manifest = fn()
        assert(type(manifest) == "table" and manifest.revision == revision, "REVISION_MISMATCH")
        local fixture = module("flpr-fixture/campaign-store/revisions/" .. revision .. ".native.lua")
        assert(fixture.revision == revision, "NATIVE_REVISION_MISMATCH")
        return { manifest = manifest, fixture = fixture }
    end
    -- Only the disposable test payload supplies this opt-in file. Installing
    -- here precedes New Game/GAM loading; first-world polling would be too late.
    local prepare_source_guard, source_guard
    local gate_config = loadfile("flpr-fixture/source-gate.lua")
    if gate_config then
        local revisions = gate_config()
        if type(revisions)=="string" then revisions={revisions} end
        assert(type(revisions)=="table","SOURCE_GATE_REVISIONS_INVALID")
        local count=0
        for key in pairs(revisions) do
            assert(type(key)=="number" and key==math.floor(key) and key>=1 and key<=#revisions,
                "SOURCE_GATE_REVISIONS_INVALID")
            count=count+1
        end
        assert(count==#revisions,"SOURCE_GATE_REVISIONS_INVALID")
        local Gate = module("override/FLPRGat.lua")
        local bundles,seen,pristine={},{},{creatures={},stores={},scripts={},templates={},spell_shield={},cursed={},auxiliary={}}
        local Cursed
        local needs_inventory=false
        for _,revision in ipairs(revisions) do
            assert(not seen[revision],"SOURCE_GATE_REVISION_DUPLICATED");seen[revision]=true
            local guarded = assert(resolve(revision), "SOURCE_GATE_REVISION_UNAVAILABLE")
            bundles[#bundles+1]=guarded
            if guarded.fixture.cursed_disguises then
                Cursed=Cursed or module('override/FLPRCur.lua')
                local assets={};pristine.cursed[revision]=assets
                for _,item in ipairs(guarded.fixture.cursed_disguises.items) do
                    local entry={variants={}};assets[item.resref]=entry
                    local prefix='flpr-fixture/campaign-store/revisions/'..revision..'.cursed/'..item.resref..'.'
                    for index=0,#item.variants do
                        local path=prefix..(index==0 and 'original' or tostring(index))..'.itm'
                        local file=assert(io.open(path,'rb'),'CURSED_ITEM_ASSET_MISSING')
                        local bytes=file:read('*a');assert(file:close())
                        if index==0 then entry.original=bytes else entry.variants[index]=bytes end
                    end
                end
            end
            if guarded.fixture.spell_shield then
                needs_inventory=true
                local entry={}
                for _,kind in ipairs({'original','effective'}) do
                    local path='flpr-fixture/campaign-store/revisions/'..revision..'.spell-shield.'..kind..'.sto'
                    local file=assert(io.open(path,'rb'),'SPELL_SHIELD_STORE_MISSING')
                    entry[kind]=file:read('*a');assert(file:close())
                end
                pristine.spell_shield[revision]=entry
            end
            for _,id in ipairs(Gate.source_ids(guarded.fixture)) do
                local endpoint=assert(guarded.fixture.sources[id])
                if endpoint.auxiliary_creatures then
                    needs_inventory=true
                    local source_index
                    for index,source in ipairs(guarded.manifest.sources) do if source.id==id then source_index=index end end
                    assert(source_index,'SOURCE_GATE_SOURCE_MISSING')
                    pristine.auxiliary[revision]=pristine.auxiliary[revision] or {}
                    local entries={};pristine.auxiliary[revision][id]=entries
                    for number,auxiliary in ipairs(endpoint.auxiliary_creatures) do
                        local path='flpr-fixture/campaign-store/revisions/'..revision..'.source-'..
                            source_index..'.duplicate-'..number..'.original.cre'
                        local file=assert(io.open(path,'rb'),'SOURCE_GATE_TEMPLATE_MISSING')
                        entries[auxiliary.resource]=file:read('*a');assert(file:close())
                    end
                end
                if endpoint.kind=='guarded-template' or endpoint.multiple_stock then needs_inventory=true end
                if endpoint.kind=='guarded-template' then
                    local source_index
                    for index,source in ipairs(guarded.manifest.sources) do if source.id==id then source_index=index end end
                    local path='flpr-fixture/campaign-store/revisions/'..revision..'.source-'..assert(source_index)..'.original.cre'
                    local file=assert(io.open(path,'rb'),'SOURCE_GATE_TEMPLATE_MISSING')
                    pristine.templates[revision]=pristine.templates[revision] or {}
                    pristine.templates[revision][id]=file:read('*a');assert(file:close())
                elseif endpoint.kind=='guarded-script' then
                    local source_index
                    for index,source in ipairs(guarded.manifest.sources) do
                        if source.id==id then source_index=index end
                    end
                    assert(source_index,'SOURCE_GATE_SOURCE_MISSING')
                    local extension=endpoint.adapter_id=='spell-key-v1' and 'spl' or 'bcs'
                    if endpoint.adapter_id=='mode2-reward-v1' then
                        extension=assert(endpoint.resource:match('^(%a+):'))
                        assert(extension=='dlg' or extension=='bcs','SOURCE_GATE_SCRIPT_KIND_INVALID')
                    end
                    local path='flpr-fixture/campaign-store/revisions/'..revision..'.source-'..source_index..'.patched.'..extension
                    local file=assert(io.open(path,'rb'),'SOURCE_GATE_SCRIPT_MISSING')
                    pristine.scripts[revision]=pristine.scripts[revision] or {}
                    pristine.scripts[revision][id]=file:read('*a'); assert(file:close())
                    if endpoint.resources then
                        local routes={}
                        for number,resource in ipairs(endpoint.resources) do
                            local kind=assert(resource:match('^(%a+):'))
                            assert(kind=='dlg' or kind=='bcs','SOURCE_GATE_SCRIPT_KIND_INVALID')
                            local route='flpr-fixture/campaign-store/revisions/'..revision..'.source-'..
                                source_index..'.route-'..number..'.patched.'..kind
                            local entry=assert(io.open(route,'rb'),'SOURCE_GATE_SCRIPT_MISSING')
                            routes[resource]=entry:read('*a');assert(entry:close())
                        end
                        pristine.scripts[revision][id]=routes
                    end
                elseif endpoint.kind=='guarded-store' then
                    local store=endpoint.store
                    assert(type(store)=='string' and #store>=1 and #store<=8 and
                        not store:find('[^A-Za-z0-9#_%-]'),'SOURCE_GATE_STORE_INVALID')
                    local source_index
                    for index,source in ipairs(guarded.manifest.sources) do
                        if source.id==id then source_index=index end
                    end
                    assert(source_index,'SOURCE_GATE_SOURCE_MISSING')
                    local prefix='flpr-fixture/campaign-store/revisions/'..revision..'.source-'..source_index
                    local entry={resource_sha256=guarded.fixture.resource_hashes[store:upper()..'.STO']}
                    for _,kind in ipairs({'original','removed'}) do
                        local file=assert(io.open(prefix..'.'..kind..'.sto','rb'),'SOURCE_GATE_STORE_MISSING')
                        entry[kind]=file:read('*a'); assert(file:close())
                    end
                    pristine.stores[revision]=pristine.stores[revision] or {}
                    pristine.stores[revision][id]=entry
                else
                    local area=endpoint.area
                    assert(type(area)=='string' and #area>=1 and #area<=8 and not area:find('[^A-Za-z0-9#_%-]'),
                        'SOURCE_GATE_AREA_INVALID')
                    area=area:upper()
                    local resource_hash=guarded.fixture.resource_hashes[area..'.ARE']
                    if not pristine[area] then
                        local file=assert(io.open("flpr-fixture/"..area..".pristine.are","rb"),"SOURCE_GATE_PRISTINE_MISSING")
                        pristine[area]={bytes=file:read("*a"),resource_sha256=resource_hash}
                        assert(file:close())
                    end
                    assert(pristine[area].resource_sha256==resource_hash,"SOURCE_GATE_RESOURCE_CHANGED")
                end
                if endpoint.kind=='guarded-creature' then
                    local source_index
                    for index,source in ipairs(guarded.manifest.sources) do
                        if source.id==id then source_index=index end
                    end
                    assert(source_index,"SOURCE_GATE_SOURCE_MISSING")
                    local prefix="flpr-fixture/campaign-store/revisions/"..revision..".source-"..source_index
                    local creature={}
                    for _,kind in ipairs({'original','removed'}) do
                        local file=assert(io.open(prefix..'.'..kind..'.cre','rb'),"SOURCE_GATE_CREATURE_MISSING")
                        creature[kind]=file:read('*a'); assert(file:close())
                    end
                    pristine.creatures[revision]=pristine.creatures[revision] or {}
                    pristine.creatures[revision][id]=creature
                end
                -- Check every profile's item before any suppression can run.
                local supported,reason=Native.supported(_G,endpoint.payload)
                assert(supported,reason)
            end
        end
        if #bundles > 0 then
            root.SourceGate = Gate.install(_G, bundles,
                module("flpr-fixture/FLPRGateAsm.lua"), module("flpr-fixture/FLPRCampaignPolicy.lua"),pristine,
                module("override/FLPRScript.lua"),needs_inventory and module("flpr-fixture/FLPRInventoryAsm.lua") or nil,Cursed)
            prepare_source_guard = function(bundle, controller)
                local unshey = 0
                for index, source in ipairs(bundle.manifest.sources) do
                    if source.record.authored and source.record.authored.source == 'unshey.fl'
                        and not controller:source_is_kept(index) then unshey = 1 end
                end
                storage:set('FLPR_QUNSHEY', unshey)
                local options = bundle.manifest.original_options or {}
                storage:set('FLPR_KANGAXX', options.kangaxx and 1 or 0)
                storage:set('FLPR_FLAIL', options.flail_heads and 1 or 0)
                return root.SourceGate:prepare_new(bundle, controller)
            end
            source_guard = function(bundle, native) return root.SourceGate:adapter(bundle, native) end
        end
    end
    root.Host = Host.new({
        core = Core, storage = storage, resolve = resolve,
        prepare_source_guard = prepare_source_guard, source_guard = source_guard,
        prepare_campaign = function(bundle, controller)
            -- The exporter writes an endpoint for every manifest source. Without
            -- one, a reward quest's selection flag would be skipped silently.
            local endpoints = bundle.fixture.sources
            assert(type(endpoints) == "table", "FIXTURE_SOURCES_MISSING")
            for _, source in ipairs(bundle.manifest.sources) do
                assert(type(endpoints[source.id]) == "table", "SOURCE_ENDPOINT_MISSING")
            end
            storage:set('FLPR_QM2_42',0)
            for index,source in ipairs(bundle.manifest.sources) do
                local flag=endpoints[source.id].reward_selected_global
                if flag then storage:set(flag,controller:source_is_kept(index) and 0 or 1) end
            end
            -- Only filled locations replace the original quest conditions.
            for _, flag in ipairs({'FLPR_CKEEP1', 'FLPR_CKEEP2', 'FLPR_CKEEP3',
                'FLPR_CSAMIA', 'FLPR_CSAFANA', 'FLPR_CPPCHST'}) do storage:set(flag, 0) end
            for _, assignment in ipairs(assert(controller:assignments())) do
                local endpoint = assignment.target_id and bundle.fixture.targets[assignment.target_id]
                if endpoint and endpoint.loot_latch then
                    storage:set(endpoint.loot_latch.activation_global, 1)
                end
            end
            return true
        end,
        next = function() return resolve(module("flpr-fixture/next.lua")) end,
        native = function(value)
            local has_shop=false
            for _,target in pairs(value.targets) do if target.kind=='store' then has_shop=true end end
            if has_shop then
                if not root.Shop then root.Shop=module('override/FLPRShop.lua').install(_G) end
                root.Shop:bind(value,root.Host.controller)
            elseif root.Shop then root.Shop:reset() end
            return Native.new(_G, value, storage:get(Core.G.seed),
                function(unit) return assert(root.Host.controller:reward_payload(unit)) end)
        end,
        seed = function()
            root.SeedSerial = root.SeedSerial + 1
            return (os.time() * 16807 + Infinity_GetClockTicks() * 48271 + root.SeedSerial) % 2147483646 + 1
        end,
        world = function() return not not Infinity_IsMenuOnStack("WORLD_ACTIONBAR") end,
        area = function()
            local area = EEex_Area_GetVisible()
            return area and area.m_resref:get():lower() or nil
        end,
        tick = function() return EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime.m_gameTime end,
        report = function(status, reason)
            if status == "complete" then
                storage:set("flpr_done", 1)
                assert(storage:get("flpr_done") == 1, "COMPLETION_MARKER_WRITE_FAILED")
                local count, kept, lost = 0, 0, 0
                for _, assignment in ipairs(assert(root.Host.controller:assignments())) do
                    if assignment.outcome == "kept" then kept = kept + 1
                    elseif assignment.outcome == "lost" then lost = lost + 1
                    else count = count + 1 end
                end
                -- The creature fixture variant names its recipients; absent means ground piles.
                local detail = root.Host.bundle.fixture.ready_detail
                if type(detail) ~= "string" then detail = "have been placed near the Candlekeep entrance." end
                local message = "Randomiser test ready: " .. count .. " rewards " .. detail
                if kept > 0 then message = message .. " " .. kept .. " left at their originals." end
                if lost > 0 then message = message .. " " .. lost .. " omitted for this adventure." end
                Infinity_DisplayString(message)
            else
                print("FLPR fixture stopped: " .. tostring(reason))
            end
        end,
    })
    -- EEex_LuaAction's NORMAL return holds the speaker before EscapeArea until
    -- its randomized inventory is ready. DONE resumes the original dialogue.
    function FLPR_Transfer(speaker, target_id)
        -- Dialogue parsing strips spaces and colons from Lua chunks. Authored
        -- Mode 2 inventory rewards pass their numeric row; expand it here.
        if type(target_id)=='number' then
            target_id='original-target:bg2:mode2:target:'..tostring(target_id)
        end
        if storage:get(Core.G.state)==0 then return -1 end -- Existing non-randomized campaign.
        local host=root.Host
        if root.Disabled or not host or not host.native or host.status=='disabled' then return 0 end
        local success,result=pcall(function()
            return host.native:transfer_inventory_reward(speaker,target_id,
                assert(host.controller:assignments()),Core.PHASE)
        end)
        if not success then
            root.Disabled='CAMPAIGN_INVENTORY_TRANSFER_FAILED:'..tostring(result)
            host:_disable(root.Disabled)
            return 0
        end
        return result
    end
    root.QA = QA.new({
        clock=Infinity_GetClockTicks, frame=Infinity_GetFrameCounter,
        menu=function(name) return not not Infinity_IsMenuOnStack(name) end,
        screen=Infinity_GetCurrentScreenName,
        transitioning=function() return not not Infinity_GetTransitionInProgress() end,
        options_active=function() return e:GetActiveEngine()==optionsScreen end,
        open_options=function()
            assert(Infinity_IsMenuOnStack("LEFT_SIDEBAR") and sidebarsGreyed~=1,"QA_SIDEBAR_UNAVAILABLE")
            local raw=assert(Infinity_FindItemWithBam("GUILS10",7),"QA_OPTIONS_BUTTON_MISSING")
            local item=EEex_PtrToUD(EEex_LightUDToPtr(raw),"uiItem")
            assert(EEex_UDToPtr(item.menu)==EEex_UDToPtr(EEex_Menu_Find("LEFT_SIDEBAR")) and
                item.area.x==3 and item.area.y==399 and item.area.w==73 and item.area.h==55,
                "QA_OPTIONS_BUTTON_UNEXPECTED")
            Infinity_ClickItem(raw)
        end,
        click=function(name,menu)
            assert(Infinity_IsMenuOnStack(menu),"QA_WRONG_MENU")
            local item=assert(nameToItem[name],"QA_BUTTON_MISSING:"..name)
            Infinity_ClickItem(item)
        end,
        saves=function() return assert(gameSaves.files) end,
        select_save=function(index) currentSave=index end,
        save_name=function(name) newSaveName=name end,
        new_save_selected=function() return currentSave==0 end,
        load_popup=function() return Popup2Button and Popup2Button.info==19531 and Popup2Button.okText=="LOAD_BUTTON" end,
        popup_info=function()
            local reference=TEXT_popup_info
            if reference==nil then return nil,nil end
            local ok,reason=pcall(getStringFromAmbiguousSource,reference)
            return reference,ok and reason or nil
        end,
        desktop_ui=function() return e:IsTouchUI()==false end,
        main_menu_popup=function()
            return Popup4Button and Popup4Button.info==16456 and
                Popup4Button.leftText=="MAIN_MENU_BUTTON" and type(Popup4Button.leftFunc)=="function" and
                Popup4Button.farLeftText=="CANCEL_BUTTON" and Popup4Button.rightText=="QUIT_BUTTON" and
                Popup4Button.farRightText=="SAVE_BUTTON"
        end,
        can_save_load=function() return canClickSaveLoad()==true end,
        world_ready=function(revision)
            local host=root.Host
            assert(host and host.status~="disabled",host and host.error or "HOST_MISSING")
            if root.Disabled or (host.status~='running' and host.status~='complete') or
                not host.controller or host.controller.manifest.revision~=revision or
                host.pending or host.guard_pending or storage:get(Core.G.journal)~=0 then return false end
            local assignments=host.controller:assignments()
            if not assignments then return false end
            local phases={}
            for _,row in ipairs(assignments) do phases[row.source]=row.source_phase end
            for index in ipairs(host.controller.manifest.sources) do
                local phase=phases[index]
                if phase~=Core.PHASE.confirmed and phase~=Core.PHASE.kept and phase~=Core.PHASE.protected then
                    return false
                end
                if phase==Core.PHASE.protected and not host.controller:source_guard_active(index) then return false end
            end
            -- Safe to exercise save/load while unvisited recipients still defer
            -- delivery. This does not change Host's gameplay completion state.
            return true
        end,
        report=function(value)
            local function quote(s)
                return '"'..tostring(s):gsub('[%z\1-\31\\"]',function(c)
                    if c=='\\' then return '\\\\' elseif c=='"' then return '\\"' end
                    return string.format('\\u%04x',string.byte(c))
                end)..'"'
            end
            local fields={}
            for _,key in ipairs({"status","kind","target","revision","step","error"}) do
                if value[key] then fields[#fields+1]=quote(key)..":"..quote(value[key]) end
            end
            fields[#fields+1]='"event_count":'..#value.events
            local file=assert(io.open("flpr-fixture/qa-status.json","w"))
            assert(file:write("{"..table.concat(fields,",").."}\n")); assert(file:close())
        end,
    })
    root.Disabled = nil
end)
if not ok then root.Disabled = tostring(failure) end

function root.Poll()
    if root.Spell and root.Spell.last_error and root.Host and not root.Disabled then
        root.Disabled='CAMPAIGN_SPELL_CALLBACK_FAILED:'..root.Spell.last_error
        root.Host:_disable(root.Disabled)
    end
    if root.Host and not root.Disabled then root.Host:poll() end
    if root.Held then
        if root.Host and root.Host.status == "complete" and not root.Disabled then
            root.Held:poll(EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime.m_gameTime)
        else root.Held:destroy() end
    end
    return ""
end

function root.NewGame()
    root.NewGameIntent = nil
    if root.Held then root.Held:destroy(); root.Held = nil end
    if root.Host then root.Host:destroy() end
    if root.SourceGate then root.SourceGate:reset() end
    local bg1 = startCampaignData[currentCampaign] and startCampaignData[currentCampaign].id == "BG1"
    startEngine:OnNewGameButtonClick()
    root.NewGameIntent = bg1 and true or nil
end

function root.AcceptCharacter(difficulty)
    if root.NewGameIntent then
        assert(not root.Disabled and root.Host, root.Disabled or "HOST_MISSING")
        assert(root.Host:capture_new("confirmed-bg1-character-accept"))
    end
    return createCharScreen:AcceptCharacter(difficulty)
end

function root.Inspect()
    return { disabled = root.Disabled, intent = root.NewGameIntent,
        screen = Infinity_GetCurrentScreenName(),
        fresh = EEex_GameState_GetGlobalInt("FLPR_FRESH"),
        committed = EEex_GameState_GetGlobalInt("flpr_state"),
        complete = EEex_GameState_GetGlobalInt("flpr_done"),
        host = root.Host and root.Host:inspect() or nil }
end

root.OnDestroyed = function()
    root.NewGameIntent = nil
    if root.Held then root.Held:destroy(); root.Held = nil end
    if root.Host then root.Host:destroy() end
end

root.OnMenusLoaded = function()
    if root.Disabled then return end
    EEex_Menu_LoadFile("FLPRRun")
    local menu = assert(EEex_Menu_Find("WORLD_ACTIONBAR"))
    local old_open = EEex_Menu_GetItemFunction(menu.reference_onOpen)
    local old_close = EEex_Menu_GetItemFunction(menu.reference_onClose)
    EEex_Menu_SetItemFunction(menu.reference_onOpen, function()
        if old_open then old_open() end
        if not Infinity_IsMenuOnStack("FLPR_RUN") then Infinity_PushMenu("FLPR_RUN") end
    end)
    EEex_Menu_SetItemFunction(menu.reference_onClose, function()
        Infinity_PopMenu("FLPR_RUN")
        if old_close then old_close() end
    end)
end

if not root.Registered then
    root.Registered = true
    EEex_GameState_AddDestroyedListener(function() FLPR.OnDestroyed() end)
    EEex_Menu_AddAfterMainFileLoadedListener(function() FLPR.OnMenusLoaded() end)
end

-- The narration hides WORLD_ACTIONBAR. Poll from its existing named text item
-- instead of adding an overlay that changes the current-screen identity.
-- Only the explicit QA script presses Done; ordinary narration is unaffected.
if not root.ChapterConsoleRegistered and type(EEex_Menu_AddBeforeUIItemRenderListener) == "function" then
    root.ChapterConsoleRegistered = true
    EEex_Menu_AddBeforeUIItemRenderListener("text_CHAPTERSCROLL_item", function()
        if EEexRemote then EEexRemote.Poll() end
    end)
end
