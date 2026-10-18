-- Isolated test automation. Only built-in queued SDL events cause transitions;
-- never call a native save/load/SelectEngine method from a render callback.
local QA = {}
local Runner = {}
Runner.__index = Runner

-- The Remote Console's own menu (EEEX_REMOTE) opens with START and WORLD_ACTIONBAR
-- and lets clicks through; back from LOAD, 2.7.3 has it above START_SP as well.
local function start_screen_ready(a)
    local screen=a.screen()
    if a.menu("WORLD_ACTIONBAR") then
        return screen=="WORLD_ACTIONBAR" or screen=="FLPR_RUN" or screen=="EEEX_REMOTE"
    elseif a.menu("START_SP") then return screen=="START_SP" or screen=="EEEX_REMOTE"
    elseif a.menu("START") then return screen=="START" or screen=="EEEX_REMOTE" end
    return false
end

function QA.new(api)
    for _, name in ipairs({"clock", "frame", "menu", "screen", "click", "open_options", "saves",
        "select_save", "save_name", "new_save_selected", "load_popup", "can_save_load", "world_ready",
        "desktop_ui", "main_menu_popup", "importing", "plain_popup"}) do
        assert(type(api[name]) == "function", "QA_API_MISSING:" .. name)
    end
    return setmetatable({api=api,status="idle",events={}},Runner)
end

function Runner:_report()
    if not self.api.report then return true end
    local ok,reason=pcall(self.api.report,self:inspect())
    if not ok then
        self.status,self.error="failed","QA_REPORT_FAILED:"..tostring(reason)
    end
    return ok
end

function Runner:_start(kind, target, revision, plain)
    assert(self.status ~= "running", "QA_ALREADY_RUNNING")
    assert(type(revision)=="string" and #revision==64 and not revision:find("[^0-9a-f]"), "QA_REVISION_INVALID")
    assert(type(target)=="string" and #target>0 and #target<=80 and not target:find("[^A-Za-z0-9 _%-]"), "QA_NAME_INVALID")
    if kind=="load" or kind=="import" then
        assert(target:match("^%d%d%d%d%d%d%d%d%d%-"), "QA_SAVE_FOLDER_REQUIRED")
    end
    local a=self.api
    if kind=="main-menu" then assert(a.desktop_ui(), "QA_DESKTOP_UI_REQUIRED") end
    if kind=="import" then
        -- Importing a party starts a new game from the single-player menu.
        assert(a.menu("START_SP") and not a.menu("WORLD_ACTIONBAR"), "QA_START_SCREEN_INVALID")
    else
        assert(a.menu("WORLD_ACTIONBAR") or (kind=="load" and (a.menu("START") or a.menu("START_SP"))),
            "QA_START_SCREEN_INVALID")
    end
    assert(start_screen_ready(a), "QA_FOREGROUND_SCREEN_INVALID")
    for _, name in ipairs({"LOAD","SAVE","SAVE_NEWSAVE","CHARGEN","CHAPTER","POPUP_INFO","POPUP_TWOBUTTON",
        "POPUP_THREEBUTTON","POPUP_FOURBUTTON","ESC_MENU"}) do
        assert(not a.menu(name), "QA_EXISTING_FLOW:"..name)
    end
    self.kind,self.target,self.revision,self.plain=kind,target,revision,plain==true
    self.status,self.step,self.error="running","begin",nil
    self.started,self.last_frame,self.events=a.clock(),nil,{}
    -- A failed start acknowledgement must not leave an active runner behind.
    if not self:_report() then error(self.error) end
end
function Runner:start_save(name,revision) self:_start("save",name,revision) end
function Runner:start_load(folder,revision) self:_start("load",folder,revision) end
-- Import Game: the party of save FOLDER starts a new game (character creation follows).
-- PLAIN accepts CIR's "Start without randomiser" when its settings have nothing
-- to randomise in this campaign; any other popup stops the runner.
function Runner:start_import(folder,revision,plain) self:_start("import",folder,revision,plain) end
function Runner:start_main_menu(revision) self:_start("main-menu","MAIN_MENU",revision) end

-- LOAD names a save by its folder; in import mode by its path, which can lead to
-- another game's profile (BG2:EE lists BG:EE's saves there). Only this game's
-- own save folder matches, so the runner never imports from another profile.
local function own_save(file_name, folder)
    return file_name==folder or file_name=="home:/save/"..folder
end

function Runner:_issue(menu, button, next_step)
    assert(self.api.menu(menu), "QA_BUTTON_MENU_MISSING:"..menu)
    -- Advance before queuing; an exception or delayed transition never retries.
    self.step=next_step
    self.events[#self.events+1]={menu=menu,button=button,frame=self.api.frame()}
    self.api.click(button,menu)
end

function Runner:_step()
    local a=self.api
    -- A stock information popup can refuse a queued save before SAVE opens.
    -- Observe it before the frame/transition gates and leave it untouched.
    if a.menu("POPUP_INFO") and a.screen()=="POPUP_INFO" then
        local reference,reason
        if type(a.popup_info)=="function" then
            local ok,reported_ref,reported_reason=pcall(a.popup_info)
            if ok then reference,reason=reported_ref,reported_reason end
        end
        if type(reason)~="string" or reason=="" then reason="reason unavailable" end
        error("QA_INFO_POPUP:"..tostring(reference or "unknown-ref")..":"..reason)
    end
    assert(a.clock()-self.started < 30000, "QA_TRANSITION_TIMEOUT:"..self.step)
    if a.transitioning and a.transitioning() then return end
    local frame=a.frame()
    if self.last_frame and frame<=self.last_frame+1 then return end
    self.last_frame=frame
    local function top(name)
        if not a.menu(name) then return false end
        if a.screen()==name then return true end
        -- Stock Options pushes its non-modal sidebar after ESC_MENU.
        return name=="ESC_MENU" and a.screen()=="LEFT_SIDEBAR_BOTTOM" and
            a.menu("LEFT_SIDEBAR_BOTTOM") and a.options_active and a.options_active()
    end
    if self.step=="begin" then
        assert(start_screen_ready(a), "QA_FOREGROUND_SCREEN_CHANGED")
        if self.kind=="import" then self:_issue("START_SP","FLPR_QA_START_IMPORT","import-list")
        elseif a.menu("WORLD_ACTIONBAR") then
            assert(self.kind~="save" or a.world_ready(self.revision), "QA_WORLD_NOT_READY")
            self.step="options"; self.events[#self.events+1]={button="world-options",frame=frame}; a.open_options()
        elseif a.menu("START_SP") then self:_issue("START_SP","FLPR_QA_START_LOAD","load-list")
        else self:_issue("START","FLPR_QA_SINGLE","single") end
    elseif self.step=="single" and top("START_SP") then
        self:_issue("START_SP","FLPR_QA_START_LOAD","load-list")
    elseif self.step=="options" and top("ESC_MENU") then
        if self.kind=="main-menu" then
            assert(a.desktop_ui(), "QA_DESKTOP_UI_REQUIRED")
            self:_issue("ESC_MENU","FLPR_QA_QUIT","quit-confirm")
        else
            assert(a.can_save_load(), "QA_SAVE_LOAD_DISABLED")
            if self.kind=="save" then self:_issue("ESC_MENU","FLPR_QA_SAVE","save-list")
            else self:_issue("ESC_MENU","FLPR_QA_LOAD","load-confirm") end
        end
    elseif self.step=="quit-confirm" and top("POPUP_FOURBUTTON") then
        assert(a.main_menu_popup(), "QA_UNEXPECTED_MAIN_MENU_POPUP")
        self:_issue("POPUP_FOURBUTTON","FLPR_QA_MAIN_MENU","main-menu")
    elseif self.step=="load-confirm" and top("POPUP_TWOBUTTON") then
        assert(a.load_popup(), "QA_UNEXPECTED_POPUP")
        self:_issue("POPUP_TWOBUTTON","FLPR_QA_POPUP_CONFIRM","load-list")
    elseif self.step=="import-list" and top("POPUP_TWOBUTTON") then
        assert(self.plain and a.plain_popup(), "QA_UNEXPECTED_POPUP")
        self:_issue("POPUP_TWOBUTTON","FLPR_QA_POPUP_CONFIRM","import-plain")
    elseif (self.step=="load-list" or self.step=="import-list" or self.step=="import-plain") and top("LOAD") then
        local importing=self.kind=="import"
        local found,count,names=nil,0,{}
        for index, save in ipairs(a.saves()) do
            if #names<8 then names[#names+1]=tostring(save.fileName) end
            if own_save(save.fileName,self.target) then count=count+1; found=index end
        end
        local problem
        if a.importing()~=importing then problem="QA_LOAD_MODE_UNEXPECTED"
        elseif count>1 then problem="QA_SAVE_AMBIGUOUS"
        elseif count==0 then problem="QA_SAVE_MISSING:"..table.concat(names,"|") end
        if problem then
            -- Back out of LOAD, where the console takes no requests, then stop.
            self.problem=problem
            self:_issue("LOAD","FLPR_QA_LOAD_BACK","left-load")
        else
            a.select_save(found)
            self:_issue("LOAD","FLPR_QA_LOAD_CONFIRM",importing and "imported" or "world")
        end
    elseif self.step=="left-load" and not a.menu("LOAD") then
        error(self.problem)
    elseif self.step=="imported" and not a.menu("LOAD") and (a.menu("CHARGEN") or a.menu("IMPORTPARTY")) then
        self.status="complete"
    elseif self.step=="save-list" and top("SAVE") then
        for _, save in ipairs(a.saves()) do
            assert(save.slotName:lower()~=self.target:lower(),"QA_REFUSE_OVERWRITE")
        end
        self:_issue("SAVE","FLPR_QA_SAVE_NEW","save-name")
    elseif self.step=="save-name" and top("SAVE_NEWSAVE") then
        assert(a.new_save_selected(),"QA_REFUSE_OVERWRITE")
        a.save_name(self.target)
        self:_issue("SAVE_NEWSAVE","FLPR_QA_SAVE_CONFIRM","world")
    elseif self.step=="world" and a.menu("WORLD_ACTIONBAR") and not a.menu("LOAD") and
        not a.menu("SAVE") and not a.menu("SAVE_NEWSAVE") and a.world_ready(self.revision) then
        self.status="complete"
    elseif self.step=="main-menu" and not a.menu("WORLD_ACTIONBAR") and
        not a.menu("ESC_MENU") and not a.menu("POPUP_FOURBUTTON") and start_screen_ready(a) then
        self.status="complete"
    end
end

function Runner:poll()
    if self.status~="running" then return end
    local before=self.status..":"..self.step
    local ok,reason=pcall(function() self:_step() end)
    if not ok then self.status,self.error="failed",tostring(reason) end
    if before~=self.status..":"..self.step then self:_report() end
end

function Runner:inspect()
    return {status=self.status,kind=self.kind,target=self.target,revision=self.revision,
        step=self.step,error=self.error,events=self.events}
end
return QA
