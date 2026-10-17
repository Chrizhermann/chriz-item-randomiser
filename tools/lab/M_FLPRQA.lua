-- Lab-only automation for disposable test games. Neither cir.tp2 nor the
-- release package contains this file or FLPRQA.lua; a lab stages both into
-- override next to an installed CIR and the EEex Remote Console.
--
-- Remote commands are evaluated during drawing. Save/load transitions can
-- release bitmap userdata still referenced by that drawing pass (live crash
-- evidence in docs/native-save-render-crash.md). Only the finite QA runner may
-- queue normal button events while SAVE, SAVE_NEWSAVE or LOAD is open;
-- arbitrary remote calls stay blocked there. Status goes to flpr-lab/.
if not EEex_Active then return end
FLPRLab = FLPRLab or {}
local lab = FLPRLab
local STATUS = "flpr-lab/qa-status.json"

local function runtime()
    local root = FLPR
    assert(type(root) == "table" and root.Core and root.Storage, "QA_RUNTIME_MISSING")
    return root, root.Core, root.Storage
end

local function api()
    return {
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
            local root, Core, storage = runtime()
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
            local file=assert(io.open(STATUS,"w"))
            assert(file:write("{"..table.concat(fields,",").."}\n")); assert(file:close())
        end,
    }
end

-- The runner is created on first use, so the load order of the M_ files does
-- not matter. Remote commands start it with FLPRLab.Runner():start_save(...).
function lab.Runner()
    if not lab.QA then lab.QA = assert(loadfile("override/FLPRQA.lua"))().new(api()) end
    return lab.QA
end

local function wrap_remote()
    if lab.RemotePollOriginal or not (type(EEexRemote) == "table" and type(EEexRemote.Poll) == "function") then
        return
    end
    lab.RemotePollOriginal = EEexRemote.Poll
    EEexRemote.Poll = function(...)
        if lab.QA then lab.QA:poll() end
        for _, name in ipairs({ "SAVE", "SAVE_NEWSAVE", "LOAD" }) do
            if Infinity_IsMenuOnStack(name) then return "" end
        end
        return lab.RemotePollOriginal(...)
    end
end

wrap_remote()
if not lab.Registered then
    lab.Registered = true
    -- Covers a Remote Console that loads after this file.
    EEex_Menu_AddAfterMainFileLoadedListener(wrap_remote)
    -- The narration hides WORLD_ACTIONBAR. Poll from its existing named text
    -- item instead of adding an overlay that changes the current-screen identity.
    if type(EEex_Menu_AddBeforeUIItemRenderListener) == "function" then
        EEex_Menu_AddBeforeUIItemRenderListener("text_CHAPTERSCROLL_item", function()
            if type(EEexRemote) == "table" and type(EEexRemote.Poll) == "function" then EEexRemote.Poll() end
        end)
    end
end
