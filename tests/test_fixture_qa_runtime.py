"""Synthetic checks for queued fixture QA actions; no native game is launched."""
from pathlib import Path
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT21


RUNTIME = (Path(__file__).resolve().parents[1] / "tools/lab/FLPRQA.lua").read_text(encoding="utf-8")
CASES = r'''
local cases={}
local revision=string.rep("a",64)
local folder="000000042-IR Proof A"
local function setup(QA,screen)
    local f={now=100,frame=10,screen=screen or "WORLD_ACTIONBAR",menus={},saves={},
        events={},selected=nil,name=nil,new_selected=true,popup=true,can=true,
        ready=true,ready_revision=revision,desktop=true,main_popup=true,
        info_ref=15729528,info_reason="You cannot save at this time."}
    f.menus[f.screen]=true
    function f:show(screen,also)
        self.screen=screen; self.menus={[screen]=true}
        for _,name in ipairs(also or {}) do self.menus[name]=true end
    end
    local api={
        clock=function() return f.now end,
        frame=function() return f.frame end,
        menu=function(name) return f.menus[name] or false end,
        screen=function() return f.screen end,
        click=function(button,menu)
            f.events[#f.events+1]={button=button,menu=menu,frame=f.frame}
            if f.click_error then error("EVENT_QUEUE_FAILED") end
        end,
        open_options=function() f.events[#f.events+1]={button="world-options",frame=f.frame} end,
        saves=function() return f.saves end,
        select_save=function(index) f.selected=index end,
        save_name=function(name) f.name=name end,
        new_save_selected=function() return f.new_selected end,
        load_popup=function() return f.popup end,
        popup_info=function() return f.info_ref,f.info_reason end,
        desktop_ui=function() return f.desktop end,
        main_menu_popup=function() return f.main_popup end,
        can_save_load=function() return f.can end,
        world_ready=function(want) return f.ready and f.ready_revision==want end,
        importing=function() return f.importing==true end,
        plain_popup=function() return f.plain_popup==true end,
    }
    f.api=api; f.runner=QA.new(api)
    function f:poll(frame) self.frame=frame or self.frame+2; self.runner:poll() end
    function f:next(screen,also) self:show(screen,also); self:poll() end
    function f:save_list()
        self.runner:start_save("IR Proof A",revision); self:poll()
        self:next("ESC_MENU",{"WORLD_ACTIONBAR"}); self:next("SAVE")
    end
    function f:load_list()
        self.runner:start_load(folder,revision); self:poll()
        if self.screen=="START" then self:next("START_SP") end
        self:next("LOAD")
    end
    return f
end
local function failed(f,reason)
    local state=f.runner:inspect()
    assert(state.status=="failed",tostring(state.error))
    assert(state.error and state.error:find(reason,1,true),tostring(state.error))
end

function cases.save_uses_observed_screens_and_waits_for_world_confirmation(QA)
    local f=setup(QA)
    f.runner:start_save("IR Proof A",revision); f:poll()
    assert(#f.events==1 and f.events[1].button=="world-options")
    for i=1,4 do f:poll() end
    assert(#f.events==1 and f.runner:inspect().status=="running")
    f:next("ESC_MENU",{"WORLD_ACTIONBAR"})
    assert(#f.events==2 and f.events[2].menu=="ESC_MENU")
    f:next("SAVE")
    assert(#f.events==3 and f.events[3].menu=="SAVE" and f.name==nil)
    f:next("SAVE_NEWSAVE",{"SAVE"})
    assert(#f.events==4 and f.events[4].menu=="SAVE_NEWSAVE" and f.name=="IR Proof A")
    assert(f.runner:inspect().status=="running")
    f:next("WORLD_ACTIONBAR",{"SAVE_NEWSAVE"})
    assert(f.runner:inspect().status=="running")
    f.ready=false; f:next("WORLD_ACTIONBAR")
    assert(f.runner:inspect().status=="running")
    f.ready=true; f:poll()
    assert(f.runner:inspect().status=="complete")
    for i=1,5 do f:poll() end
    assert(#f.events==4)
end

function cases.repeated_or_adjacent_frames_never_queue_next_action(QA)
    local f=setup(QA)
    f.runner:start_save("IR Proof A",revision); f:poll(10)
    f:show("ESC_MENU",{"WORLD_ACTIONBAR"})
    for _,frame in ipairs({10,10,11,9,11}) do f:poll(frame) end
    assert(#f.events==1)
    f:poll(12); assert(#f.events==2)
    f:show("SAVE"); f:poll(12); f:poll(13)
    assert(#f.events==2)
    f:poll(14); assert(#f.events==3)
end

function cases.background_menu_does_not_authorize_click(QA)
    local f=setup(QA)
    f.runner:start_save("IR Proof A",revision); f:poll()
    f:next("UNRELATED_MODAL",{"ESC_MENU","WORLD_ACTIONBAR"})
    assert(#f.events==1 and f.runner:inspect().status=="running")
    f:next("ESC_MENU",{"WORLD_ACTIONBAR"}); assert(#f.events==2)
end

function cases.foreground_info_popup_stops_queued_save_before_timeout_and_never_clicks_again(QA)
    local f=setup(QA)
    f.runner:start_save("IR Proof A",revision); f:poll()
    f:next("ESC_MENU",{"WORLD_ACTIONBAR"})
    assert(#f.events==2 and f.events[2].button=="FLPR_QA_SAVE")
    f.now=f.now+30001 -- The popup reason must outrank the old timeout.
    f:next("POPUP_INFO",{"ESC_MENU","WORLD_ACTIONBAR"})
    failed(f,"QA_INFO_POPUP:15729528:You cannot save at this time.")
    assert(f.runner:inspect().step=="save-list" and #f.events==2)
    f:next("SAVE"); f:next("SAVE_NEWSAVE"); f:poll()
    assert(#f.events==2 and f.runner:inspect().status=="failed")
end

function cases.background_info_popup_does_not_mask_unrelated_foreground(QA)
    local f=setup(QA)
    f.runner:start_save("IR Proof A",revision); f:poll()
    f:next("UNRELATED_MODAL",{"POPUP_INFO","ESC_MENU","WORLD_ACTIONBAR"})
    assert(f.runner:inspect().status=="running" and #f.events==1)
    f:next("ESC_MENU",{"WORLD_ACTIONBAR"})
    assert(f.runner:inspect().status=="running" and #f.events==2)
end

function cases.info_popup_without_reason_still_has_diagnostic_code_and_ref(QA)
    local f=setup(QA)
    f.runner:start_save("IR Proof A",revision); f:poll()
    f:next("ESC_MENU",{"WORLD_ACTIONBAR"})
    f.info_reason=nil; f:next("POPUP_INFO",{"ESC_MENU"})
    failed(f,"QA_INFO_POPUP:15729528:reason unavailable")
    assert(#f.events==2)
    local absent=setup(QA)
    absent.api.popup_info=nil
    absent.runner:start_save("IR Proof A",revision); absent:poll()
    absent:next("POPUP_INFO")
    failed(absent,"QA_INFO_POPUP:unknown-ref:reason unavailable")
    assert(#absent.events==1)
end

function cases.completed_world_must_match_requested_revision(QA)
    local f=setup(QA,"START_SP")
    f.saves={{fileName=folder,slotName="IR Proof A"}}
    f:load_list(); assert(#f.events==2 and f.selected==1)
    f.ready_revision=string.rep("b",64); f:next("WORLD_ACTIONBAR")
    assert(f.runner:inspect().status=="running")
    f.ready_revision=revision; f:poll()
    assert(f.runner:inspect().status=="complete")
end

function cases.load_from_start_selects_exact_folder_before_queued_confirmation(QA)
    local f=setup(QA,"START")
    f.saves={{fileName="000000043-IR Proof A",slotName="IR Proof A"},
        {fileName=folder,slotName="IR Proof A"}}
    f:load_list()
    assert(#f.events==3 and f.events[1].menu=="START" and f.events[2].menu=="START_SP")
    assert(f.events[3].menu=="LOAD" and f.selected==2)
    f:next("WORLD_ACTIONBAR"); assert(f.runner:inspect().status=="complete")
end

function cases.load_from_singleplayer_omits_campaign_menu_action(QA)
    local f=setup(QA,"START_SP")
    f.saves={{fileName=folder,slotName="IR Proof A"}}
    f:load_list()
    assert(#f.events==2 and f.events[1].menu=="START_SP" and f.events[2].menu=="LOAD")
end

function cases.world_load_requires_expected_popup_then_exact_save(QA)
    local f=setup(QA)
    f.ready=false -- A load can replace an unfinished current campaign.
    f.saves={{fileName=folder,slotName="IR Proof A"}}
    f.runner:start_load(folder,revision); f:poll()
    f:next("ESC_MENU",{"WORLD_ACTIONBAR"}); assert(#f.events==2)
    f:next("POPUP_TWOBUTTON",{"ESC_MENU","WORLD_ACTIONBAR"})
    assert(#f.events==3 and f.events[3].menu=="POPUP_TWOBUTTON")
    f:next("LOAD"); assert(f.selected==1 and #f.events==4)
    f.ready=true; f:next("WORLD_ACTIONBAR")
    assert(f.runner:inspect().status=="complete")
end

function cases.unrelated_confirmation_popup_is_not_accepted(QA)
    local f=setup(QA)
    f.runner:start_load(folder,revision); f:poll(); f:next("ESC_MENU")
    f.popup=false; f:next("POPUP_TWOBUTTON")
    failed(f,"QA_UNEXPECTED_POPUP"); assert(#f.events==2)
    f.popup=true; f:poll(); assert(#f.events==2)
end

function cases.existing_save_name_refuses_case_insensitive_overwrite(QA)
    local f=setup(QA)
    f.saves={{fileName=folder,slotName="ir proof a"}}
    f:save_list(); failed(f,"QA_REFUSE_OVERWRITE")
    assert(#f.events==2 and f.name==nil)
end

function cases.non_new_save_selection_refuses_overwrite(QA)
    local f=setup(QA); f:save_list()
    f.new_selected=false; f:next("SAVE_NEWSAVE",{"SAVE"})
    failed(f,"QA_REFUSE_OVERWRITE"); assert(#f.events==3 and f.name==nil)
end

function cases.missing_save_is_not_replaced_by_similar_name(QA)
    local f=setup(QA,"START_SP")
    f.saves={{fileName="000000043-IR Proof A",slotName="IR Proof A"}}
    f:load_list()
    -- LOAD takes no console requests, so the runner leaves it before it stops.
    assert(f.selected==nil and #f.events==2 and f.events[2].button=="FLPR_QA_LOAD_BACK")
    assert(f.runner:inspect().status=="running")
    f:next("START_SP"); failed(f,"QA_SAVE_MISSING:000000043-IR Proof A")
    for i=1,3 do f:poll() end
    assert(#f.events==2)
end

function cases.ambiguous_save_folder_is_not_selected(QA)
    local f=setup(QA,"START_SP")
    f.saves={{fileName=folder,slotName="A"},{fileName=folder,slotName="B"}}
    f:load_list()
    assert(f.selected==nil and #f.events==2 and f.events[2].button=="FLPR_QA_LOAD_BACK")
    f:next("START"); failed(f,"QA_SAVE_AMBIGUOUS")
    assert(#f.events==2)
end

function cases.import_selects_the_folder_in_import_mode_and_completes_in_character_creation(QA)
    local f=setup(QA,"START_SP"); f.importing=true
    f.saves={{fileName="000000043-IR Proof A",slotName="IR Proof A"},{fileName=folder,slotName="IR Proof A"}}
    f.runner:start_import(folder,revision); f:poll()
    assert(#f.events==1 and f.events[1].menu=="START_SP" and f.events[1].button=="FLPR_QA_START_IMPORT")
    f:next("LOAD")
    assert(#f.events==2 and f.events[2].button=="FLPR_QA_LOAD_CONFIRM" and f.selected==2)
    f:next("LOAD",{"CHARGEN"}); assert(f.runner:inspect().status=="running")
    f:next("CHARGEN"); assert(f.runner:inspect().status=="complete")
    for i=1,3 do f:poll() end
    assert(#f.events==2)
end

function cases.import_matches_this_profiles_save_path_but_never_another_profile(QA)
    -- In import mode LOAD names saves by path; BG2:EE also lists BG:EE's profile.
    local f=setup(QA,"START_SP"); f.importing=true
    f.saves={{fileName="home:/../Baldur's Gate - Enhanced Edition/save/"..folder,slotName="IR Proof A"},
        {fileName="home:/save/"..folder,slotName="IR Proof A"}}
    f.runner:start_import(folder,revision); f:poll(); f:next("LOAD")
    assert(#f.events==2 and f.events[2].button=="FLPR_QA_LOAD_CONFIRM" and f.selected==2)
    local g=setup(QA,"START_SP"); g.importing=true
    g.saves={{fileName="home:/../Baldur's Gate - Enhanced Edition/save/"..folder,slotName="IR Proof A"}}
    g.runner:start_import(folder,revision); g:poll(); g:next("LOAD")
    assert(g.selected==nil and #g.events==2 and g.events[2].button=="FLPR_QA_LOAD_BACK")
    g:next("START_SP"); failed(g,"QA_SAVE_MISSING:home:/../Baldur's Gate - Enhanced Edition/save/"..folder)
end

function cases.import_takes_bgees_profile_only_when_named(QA)
    -- BG2:EE's Import Game from a BG:EE save: the runner names that profile explicitly.
    local f=setup(QA,"START_SP"); f.importing=true
    f.saves={{fileName="home:/save/"..folder,slotName="IR Proof A"},
        {fileName="home:/../Baldur's Gate - Enhanced Edition/save/"..folder,slotName="IR Proof A"}}
    f.runner:start_import(folder,revision,nil,"bgee"); f:poll(); f:next("LOAD")
    assert(#f.events==2 and f.events[2].button=="FLPR_QA_LOAD_CONFIRM" and f.selected==2)
    local g=setup(QA,"START_SP"); g.importing=true
    g.saves={{fileName="home:/save/"..folder,slotName="IR Proof A"}}
    g.runner:start_import(folder,revision,nil,"bgee"); g:poll(); g:next("LOAD")
    assert(g.selected==nil and #g.events==2 and g.events[2].button=="FLPR_QA_LOAD_BACK")
    g:next("START_SP"); failed(g,"QA_SAVE_MISSING:home:/save/"..folder)
    for _,profile in ipairs({"bg2ee","home:/..",""}) do
        local h=setup(QA,"START_SP")
        assert(not pcall(function() h.runner:start_import(folder,revision,nil,profile) end))
        assert(#h.events==0 and h.runner:inspect().status=="idle")
    end
    local k=setup(QA,"START_SP")
    assert(not pcall(function() k.runner:_start("load",folder,revision,nil,"bgee") end))
    assert(#k.events==0 and k.runner:inspect().status=="idle")
end

function cases.import_accepts_cirs_start_plain_popup_only_when_asked(QA)
    -- A campaign CIR's settings have nothing for (BG:EE's SoD with BG1 lists) asks first.
    local f=setup(QA,"START_SP"); f.importing=true; f.plain_popup=true
    f.saves={{fileName="home:/save/"..folder,slotName="IR Proof A"}}
    f.runner:start_import(folder,revision,true); f:poll()
    f:next("POPUP_TWOBUTTON",{"START"})
    assert(#f.events==2 and f.events[2].button=="FLPR_QA_POPUP_CONFIRM")
    f:next("LOAD"); assert(#f.events==3 and f.events[3].button=="FLPR_QA_LOAD_CONFIRM" and f.selected==1)
    f:next("CHARGEN"); assert(f.runner:inspect().status=="complete")
    for _,case in ipairs({{plain=nil,popup=true},{plain=true,popup=false}}) do
        local g=setup(QA,"START_SP"); g.importing=true; g.plain_popup=case.popup
        g.runner:start_import(folder,revision,case.plain); g:poll()
        g:next("POPUP_TWOBUTTON",{"START"}); failed(g,"QA_UNEXPECTED_POPUP")
        assert(#g.events==1)
    end
end

function cases.load_screen_in_the_other_mode_is_left_without_selecting(QA)
    -- Import Game and Load Game share LOAD; its mode decides what confirming does.
    local f=setup(QA,"START_SP"); f.importing=false
    f.saves={{fileName=folder,slotName="IR Proof A"}}
    f.runner:start_import(folder,revision); f:poll(); f:next("LOAD")
    assert(f.selected==nil and #f.events==2 and f.events[2].button=="FLPR_QA_LOAD_BACK")
    f:next("START_SP"); failed(f,"QA_LOAD_MODE_UNEXPECTED")
    local g=setup(QA,"START_SP"); g.importing=true
    g.saves={{fileName=folder,slotName="IR Proof A"}}
    g:load_list()
    assert(g.selected==nil and #g.events==2 and g.events[2].button=="FLPR_QA_LOAD_BACK")
    g:next("START_SP"); failed(g,"QA_LOAD_MODE_UNEXPECTED")
end

function cases.import_starts_only_from_the_singleplayer_menu_with_a_save_folder(QA)
    for _,screen in ipairs({"WORLD_ACTIONBAR","START"}) do
        local f=setup(QA,screen)
        assert(not pcall(function() f.runner:start_import(folder,revision) end))
        assert(#f.events==0 and f.runner:inspect().status=="idle")
    end
    local f=setup(QA,"START_SP")
    assert(not pcall(function() f.runner:start_import("IR Proof A",revision) end))
    assert(#f.events==0 and f.runner:inspect().status=="idle")
end

function cases.console_menu_above_the_singleplayer_menu_is_accepted(QA)
    -- Back from LOAD, 2.7.3 has the Remote Console's own menu above START_SP.
    local f=setup(QA,"EEEX_REMOTE"); f.menus.START=true; f.menus.START_SP=true; f.importing=true
    f.runner:start_import(folder,revision); f:poll()
    assert(#f.events==1 and f.events[1].menu=="START_SP")
    local g=setup(QA,"UNRELATED_MODAL"); g.menus.START_SP=true
    assert(not pcall(function() g.runner:start_import(folder,revision) end))
    assert(#g.events==0)
end

function cases.timeout_stops_without_replaying_queued_action(QA)
    local f=setup(QA); f.runner:start_save("IR Proof A",revision); f:poll()
    f.now=f.now+30001; f:poll()
    failed(f,"QA_TRANSITION_TIMEOUT"); assert(#f.events==1)
    f:next("ESC_MENU"); f:poll(); assert(#f.events==1)
end

function cases.click_exception_fails_without_retry_even_if_event_was_queued(QA)
    local f=setup(QA,"START_SP"); f.click_error=true
    f.runner:start_load(folder,revision); f:poll()
    failed(f,"EVENT_QUEUE_FAILED"); assert(#f.events==1)
    f.click_error=false; f:poll(); f:next("LOAD")
    assert(#f.events==1 and f.selected==nil)
    assert(#f.runner:inspect().events==1)
end

function cases.disabled_save_load_stops_before_menu_button(QA)
    local f=setup(QA); f.runner:start_save("IR Proof A",revision); f:poll()
    f.can=false; f:next("ESC_MENU")
    failed(f,"QA_SAVE_LOAD_DISABLED"); assert(#f.events==1)
end

function cases.unfinished_campaign_cannot_be_saved(QA)
    local f=setup(QA); f.ready=false
    f.runner:start_save("IR Proof A",revision); f:poll()
    failed(f,"QA_WORLD_NOT_READY"); assert(#f.events==0)
end

function cases.start_validation_refuses_incomplete_flow_or_invalid_target(QA)
    for _,menu in ipairs({"LOAD","SAVE","SAVE_NEWSAVE","CHARGEN","CHAPTER","POPUP_INFO","POPUP_TWOBUTTON",
        "POPUP_THREEBUTTON","POPUP_FOURBUTTON","ESC_MENU"}) do
        local f=setup(QA); f.menus[menu]=true
        assert(not pcall(function() f.runner:start_save("IR Proof A",revision) end))
        assert(#f.events==0 and f.runner:inspect().status=="idle")
    end
    for _,name in ipairs({"","../test","a/b","a\\b",string.rep("a",81)}) do
        local f=setup(QA)
        assert(not pcall(function() f.runner:start_save(name,revision) end))
        assert(#f.events==0)
    end
    local f=setup(QA,"START_SP")
    assert(not pcall(function() f.runner:start_load("IR Proof A",revision) end))
    assert(not pcall(function() f.runner:start_load(folder,"not-a-revision") end))
    assert(#f.events==0)
end

function cases.active_run_cannot_be_replaced_or_have_its_target_changed(QA)
    local f=setup(QA); f.runner:start_save("IR Proof A",revision); f:poll()
    assert(not pcall(function() f.runner:start_load(folder,revision) end))
    local state=f.runner:inspect()
    assert(state.kind=="save" and state.target=="IR Proof A" and state.status=="running")
    assert(#f.events==1)
end

function cases.failed_start_report_stops_before_any_ui_action(QA)
    local f=setup(QA); local reports=0
    f.api.report=function() reports=reports+1; error("DISK_WRITE_FAILED") end
    assert(not pcall(function() f.runner:start_save("IR Proof A",revision) end))
    failed(f,"QA_REPORT_FAILED"); assert(#f.events==0 and reports==1)
    for i=1,5 do f:poll() end
    assert(#f.events==0 and reports==1)
end

function cases.report_failure_after_queued_event_is_contained_and_never_replayed(QA)
    local f=setup(QA); local reports=0
    f.api.report=function()
        reports=reports+1
        if reports>1 then error("DISK_WRITE_FAILED") end
    end
    f.runner:start_save("IR Proof A",revision)
    assert(pcall(function() f:poll() end))
    failed(f,"QA_REPORT_FAILED"); assert(#f.events==1 and reports==2)
    f:next("ESC_MENU"); f:poll()
    assert(#f.events==1 and reports==2)
end
function cases.unknown_foreground_modal_blocks_start_and_first_event(QA)
    local f=setup(QA)
    f.screen="UNRELATED_MODAL"
    assert(not pcall(function() f.runner:start_save("IR Proof A",revision) end))
    assert(#f.events==0)
    f.screen="WORLD_ACTIONBAR"; f.runner:start_save("IR Proof A",revision)
    f.screen="UNRELATED_MODAL"; f:poll()
    failed(f,"QA_FOREGROUND_SCREEN_CHANGED"); assert(#f.events==0)
end
function cases.stock_options_sidebar_requires_matching_active_engine(QA)
    local f=setup(QA)
    f.api.options_active=function() return f.options_active end
    f.runner:start_save("IR Proof A",revision); f:poll()
    f:next("LEFT_SIDEBAR_BOTTOM",{"ESC_MENU"})
    assert(#f.events==1)
    f.options_active=true; f:poll()
    assert(#f.events==2 and f.events[2].menu=="ESC_MENU")
end
function cases.animation_must_end_before_queuing_the_next_button(QA)
    local f=setup(QA,"START")
    f.api.transitioning=function() return f.animating end
    f.runner:start_load(folder,revision); f:poll()
    f.animating=true; f:next("START_SP")
    for i=1,5 do f:poll() end
    assert(#f.events==1)
    f.animating=false; f:poll(); assert(#f.events==2)
end
function cases.main_menu_uses_only_quit_popup_main_menu_branch_and_observes_completion(QA)
    for _,destination in ipairs({"START","START_SP"}) do
        local f=setup(QA)
        f.runner:start_main_menu(revision); f:poll()
        assert(#f.events==1 and f.events[1].button=="world-options")
        f:next("ESC_MENU",{"WORLD_ACTIONBAR"})
        assert(#f.events==2 and f.events[2].button=="FLPR_QA_QUIT")
        f:next("POPUP_FOURBUTTON",{"ESC_MENU","WORLD_ACTIONBAR"})
        assert(#f.events==3 and f.events[3].button=="FLPR_QA_MAIN_MENU")
        for i=1,5 do f:poll() end
        assert(#f.events==3 and f.runner:inspect().status=="running")
        f:next(destination,{"POPUP_FOURBUTTON"})
        assert(f.runner:inspect().status=="running")
        f:next(destination,{"WORLD_ACTIONBAR"})
        assert(f.runner:inspect().status=="running")
        f:next(destination,{"ESC_MENU"})
        assert(f.runner:inspect().status=="running")
        f:next(destination)
        assert(f.runner:inspect().status=="complete")
        for i=1,5 do f:poll() end
        assert(#f.events==3)
    end
end
function cases.main_menu_allows_failed_campaign_but_requires_desktop_and_valid_popup(QA)
    local f=setup(QA); f.desktop=false
    assert(not pcall(function() f.runner:start_main_menu(revision) end))
    assert(#f.events==0)
    f=setup(QA,"START")
    assert(not pcall(function() f.runner:start_main_menu(revision) end))
    assert(#f.events==0)
    f=setup(QA); f.api.world_ready=function() error('CAMPAIGN_DISABLED') end
    f.runner:start_main_menu(revision); f:poll()
    assert(f.runner:inspect().status=='running' and #f.events==1)
    f:next('ESC_MENU');f:next('POPUP_FOURBUTTON');f:next('START')
    assert(f.runner:inspect().status=='complete' and #f.events==3)
    f=setup(QA); f.runner:start_main_menu(revision); f:poll()
    f:next("ESC_MENU"); f.main_popup=false; f:next("POPUP_FOURBUTTON")
    failed(f,"QA_UNEXPECTED_MAIN_MENU_POPUP"); assert(#f.events==2)
    f.main_popup=true; f:poll(); assert(#f.events==2)
end
function cases.main_menu_respects_animation_foreground_and_once_only_queue_failures(QA)
    local f=setup(QA)
    f.api.transitioning=function() return f.animating end
    f.runner:start_main_menu(revision); f:poll(); f:next("ESC_MENU")
    f:next("UNRELATED_MODAL",{"POPUP_FOURBUTTON"}); assert(#f.events==2)
    f.animating=true; f:next("POPUP_FOURBUTTON")
    for i=1,3 do f:poll() end
    assert(#f.events==2)
    f.animating=false; f.click_error=true; f:poll()
    failed(f,"EVENT_QUEUE_FAILED"); assert(#f.events==3)
    f.click_error=false; f:next("START"); f:poll()
    assert(#f.events==3 and f.runner:inspect().status=="failed")
end
function cases.main_menu_missing_transition_times_out_without_replaying(QA)
    local f=setup(QA)
    f.runner:start_main_menu(revision); f:poll(); f:next("ESC_MENU"); f:next("POPUP_FOURBUTTON")
    f.now=f.now+30001; f:poll()
    failed(f,"QA_TRANSITION_TIMEOUT"); assert(#f.events==3)
    f:next("START"); assert(f.runner:inspect().status=="failed" and #f.events==3)
end
return cases
'''

CASE_NAMES = sorted(Lua51().execute(CASES).keys())


def make_case(name):
    def run(self):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.execute(CASES)[name](lua.execute(RUNTIME))
    return run


for label, runtime in (("Lua51", Lua51), ("Lua53", Lua53), ("LuaJIT21", LuaJIT21)):
    globals()[label + "FixtureQATests"] = type(
        label + "FixtureQATests", (unittest.TestCase,),
        {"runtime": runtime, **{"test_" + name: make_case(name) for name in CASE_NAMES}},
    )


if __name__ == "__main__":
    unittest.main()
