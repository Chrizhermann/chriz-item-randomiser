-- Explicitly staged only by the isolated fixture installer, never normal TP2.
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
        assert(type(revisions)=="table" and #revisions>0,"SOURCE_GATE_REVISIONS_INVALID")
        local count=0
        for key in pairs(revisions) do
            assert(type(key)=="number" and key==math.floor(key) and key>=1 and key<=#revisions,
                "SOURCE_GATE_REVISIONS_INVALID")
            count=count+1
        end
        assert(count==#revisions,"SOURCE_GATE_REVISIONS_INVALID")
        local Gate = module("override/FLPRGat.lua")
        local bundles,seen,pristine={},{},{creatures={},stores={},scripts={}}
        for _,revision in ipairs(revisions) do
            assert(not seen[revision],"SOURCE_GATE_REVISION_DUPLICATED");seen[revision]=true
            local guarded = assert(resolve(revision), "SOURCE_GATE_REVISION_UNAVAILABLE")
            bundles[#bundles+1]=guarded
            for _,id in ipairs(Gate.source_ids(guarded.fixture)) do
                local endpoint=assert(guarded.fixture.sources[id])
                if endpoint.kind=='guarded-script' then
                    local source_index
                    for index,source in ipairs(guarded.manifest.sources) do
                        if source.id==id then source_index=index end
                    end
                    assert(source_index,'SOURCE_GATE_SOURCE_MISSING')
                    local path='flpr-fixture/campaign-store/revisions/'..revision..'.source-'..source_index..'.patched.bcs'
                    local file=assert(io.open(path,'rb'),'SOURCE_GATE_SCRIPT_MISSING')
                    pristine.scripts[revision]=pristine.scripts[revision] or {}
                    pristine.scripts[revision][id]=file:read('*a'); assert(file:close())
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
        root.SourceGate = Gate.install(_G, bundles,
            module("flpr-fixture/FLPRGateAsm.lua"), module("flpr-fixture/FLPRCampaignPolicy.lua"),pristine,
            module("override/FLPRScript.lua"))
        prepare_source_guard = function(bundle, controller) return root.SourceGate:prepare_new(bundle, controller) end
        source_guard = function(bundle, native) return root.SourceGate:adapter(bundle, native) end
    end
    root.Host = Host.new({
        core = Core, storage = storage, resolve = resolve,
        prepare_source_guard = prepare_source_guard, source_guard = source_guard,
        next = function() return resolve(module("flpr-fixture/next.lua")) end,
        native = function(value)
            local has_shop=false
            for _,target in pairs(value.targets) do if target.kind=='store' then has_shop=true end end
            if has_shop then
                if not root.Shop then root.Shop=module('override/FLPRShop.lua').install(_G) end
                root.Shop:bind(value,root.Host.controller)
            elseif root.Shop then root.Shop:reset() end
            return Native.new(_G, value)
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
                local count = #assert(root.Host.controller:assignments())
                -- The creature fixture variant names its recipients; absent means ground piles.
                local detail = root.Host.bundle.fixture.ready_detail
                if type(detail) ~= "string" then detail = "have been placed near the Candlekeep entrance." end
                Infinity_DisplayString("Randomiser test ready: " .. count .. " test weapons " .. detail)
            else
                print("FLPR fixture stopped: " .. tostring(reason))
            end
        end,
    })
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
            return host.status=="complete" and host.controller.manifest.revision==revision
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
