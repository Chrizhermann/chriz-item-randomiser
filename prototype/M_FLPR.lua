-- Explicitly staged only by the isolated fixture installer, never normal TP2.
if not EEex_Active then return end
FLPR = FLPR or {}
local root = FLPR
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
    root.Host = Host.new({
        core = Core, storage = storage, resolve = resolve,
        next = function() return resolve(module("flpr-fixture/next.lua")) end,
        native = function(value) return Native.new(_G, value) end,
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
                Infinity_DisplayString("Randomiser test ready: " .. count .. " test weapons have been placed near the Candlekeep entrance.")
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
    return ""
end

function root.NewGame()
    root.NewGameIntent = nil
    if root.Host then root.Host:destroy() end
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
