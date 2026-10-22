-- Installed by cir.tp2. Lab automation lives in the
-- separate lab-only tools/lab/M_FLPRQA.lua and is never installed for players.
if not EEex_Active then return end
FLPR = FLPR or {}
local root = FLPR
if root.Held then root.Held:destroy(); root.Held = nil end
if root.Host then root.Host:destroy() end
root.Disabled, root.Host, root.NewGameIntent = "INITIALIZING", nil, nil
root.SeedSerial = root.SeedSerial or 0
-- Applied configurations, kept campaign revisions and pristine resource copies.
local DATA = "randomiser-campaigns/"
root.DATA = DATA
-- Whole-block copies into memory the runtime owns (LuaJIT ffi); without it
-- the modules copy through EEex byte by byte.
if root.Memory == nil then
    local has_ffi, ffi = pcall(require, "ffi")
    root.Memory = has_ffi and {
        write = function(address, bytes) ffi.copy(ffi.cast("void *", address), bytes, #bytes) end,
        read = function(address, length) return ffi.string(ffi.cast("const char *", address), length) end,
    } or false
end

-- Extra arguments reach the module chunk as `...` (the hook location table).
local function module(path, ...)
    return assert(loadfile(path))(...)
end

-- Player-facing text comes from M_FLPRUI.lua, installed with this file.
local function text(key)
    if type(FLPRUI_Text) == "function" then return FLPRUI_Text(key) end
    return key
end

-- The details behind a player-facing message, for bug reports. The messages
-- name this file; only the latest failure is kept.
local function record(context, reason)
    local line = context .. ": " .. tostring(reason)
    print("Chriz Item Randomiser " .. line)
    local file = io.open(DATA .. "last-error.txt", "w")
    if file then
        file:write(os.date("%Y-%m-%d %H:%M:%S "), line, "\n")
        file:close()
    end
end

-- Every native hook location is found in the running executable and its code
-- checked before patching (FLPRSite.lua), so a code mismatch means the runtime
-- could not find or confirm its hooks in this game's engine.
local function failure_message(reason)
    return tostring(reason):find("CODE_MISMATCH", 1, true) and "unsupported" or "setup_failed"
end

-- Tells the player once per loaded game when its randomised items can no
-- longer be handled: in the message log at once and, as a line there is easily
-- missed, in the engine's info popup as soon as the world screen is up (not
-- over the start screen, character creation or a loading screen). A game that
-- was never randomised needs no message.
local function warn_stopped()
    if root.Warned == true or EEex_GameState_GetGlobalInt("flpr_state") == 0 then return end
    if not root.Warned then
        root.Warned = "logged"
        Infinity_DisplayString(text("stopped"))
    end
    if Infinity_IsMenuOnStack("WORLD_ACTIONBAR") then
        root.Warned = true
        if type(FLPRUI_Tell) == "function" then FLPRUI_Tell("stopped") end
    end
end

local ok, failure = pcall(function()
    -- Item Randomiser's Mode 1 and Mode 2 remove items when installed and assign
    -- them themselves. cir.tp2 refuses to install after them, but Item
    -- Randomiser does not know this mod and can be installed later.
    for _, marker in ipairs({ "flrandomiser1.mrk", "flrandomiser2.mrk" }) do
        local file = io.open("override/" .. marker, "rb")
        if file then
            file:close()
            error("ITEM_RANDOMISER_MODE_INSTALLED:override/" .. marker, 0)
        end
    end
    local Core = module("override/FLPRCore.lua")
    local Site = module("override/FLPRSite.lua")
    local Globals = module("override/FLPRGlob.lua")
    local Native = module("override/FLPRNat.lua")
    local Host = module("override/FLPRHost.lua")
    if not root.Spell then
        local spell=loadfile('override/FLPRSpell.lua')
        if spell then root.Spell=spell().install(_G) end
    end
    local storage = assert(Globals.new(_G))
    -- Read-only handles for diagnostics, such as the lab automation module.
    root.Core, root.Storage = Core, storage
    local function resolve(revision)
        if type(revision) ~= "string" or #revision ~= 64 or revision:find("[^0-9a-f]") then return nil end
        local fn = loadfile(DATA.."campaign-store/revisions/" .. revision .. ".lua")
        if not fn then return nil end
        local manifest = fn()
        assert(type(manifest) == "table" and manifest.revision == revision, "REVISION_MISMATCH")
        local fixture = module(DATA.."campaign-store/revisions/" .. revision .. ".native.lua")
        assert(fixture.revision == revision, "NATIVE_REVISION_MISMATCH")
        return { manifest = manifest, fixture = fixture }
    end
    -- An applied configuration lists every revision whose sources the gate
    -- guards. The gate's hooks are placed here, before New Game or any GAM
    -- loading (first-world polling would be too late); a revision's checks are
    -- built when a game needs them, so launch time does not grow with Applies.
    local prepare_source_guard, source_guard
    local gate_config = loadfile(DATA.."source-gate.lua")
    if gate_config then
        local revisions = gate_config()
        if type(revisions)=="string" then revisions={revisions} end
        assert(type(revisions)=="table","SOURCE_GATE_REVISIONS_INVALID")
        local count,seen=0,{}
        for key in pairs(revisions) do
            assert(type(key)=="number" and key==math.floor(key) and key>=1 and key<=#revisions,
                "SOURCE_GATE_REVISIONS_INVALID")
            count=count+1
        end
        assert(count==#revisions,"SOURCE_GATE_REVISIONS_INVALID")
        for _,revision in ipairs(revisions) do
            assert(not seen[revision],"SOURCE_GATE_REVISION_DUPLICATED");seen[revision]=true
        end
        local Gate = module("override/FLPRGat.lua", Site)
        local pristine={creatures={},stores={},scripts={},templates={},spell_shield={},cursed={},auxiliary={}}
        -- One revision's manifest and the copies its checks compare against.
        local function load_revision(revision)
            local guarded = assert(resolve(revision), "SOURCE_GATE_REVISION_UNAVAILABLE")
            if guarded.fixture.cursed_disguises then
                local assets={};pristine.cursed[revision]=assets
                for _,item in ipairs(guarded.fixture.cursed_disguises.items) do
                    local entry={variants={}};assets[item.resref]=entry
                    local prefix=DATA..'campaign-store/revisions/'..revision..'.cursed/'..item.resref..'.'
                    for index=0,#item.variants do
                        local path=prefix..(index==0 and 'original' or tostring(index))..'.itm'
                        local file=assert(io.open(path,'rb'),'CURSED_ITEM_ASSET_MISSING')
                        local bytes=file:read('*a');assert(file:close())
                        if index==0 then entry.original=bytes else entry.variants[index]=bytes end
                    end
                end
            end
            if guarded.fixture.spell_shield then
                local entry={}
                for _,kind in ipairs({'original','effective'}) do
                    local path=DATA..'campaign-store/revisions/'..revision..'.spell-shield.'..kind..'.sto'
                    local file=assert(io.open(path,'rb'),'SPELL_SHIELD_STORE_MISSING')
                    entry[kind]=file:read('*a');assert(file:close())
                end
                pristine.spell_shield[revision]=entry
            end
            for _,id in ipairs(Gate.source_ids(guarded.fixture)) do
                local endpoint=assert(guarded.fixture.sources[id])
                if endpoint.auxiliary_creatures then
                    local source_index
                    for index,source in ipairs(guarded.manifest.sources) do if source.id==id then source_index=index end end
                    assert(source_index,'SOURCE_GATE_SOURCE_MISSING')
                    pristine.auxiliary[revision]=pristine.auxiliary[revision] or {}
                    local entries={};pristine.auxiliary[revision][id]=entries
                    for number,auxiliary in ipairs(endpoint.auxiliary_creatures) do
                        local path=DATA..'campaign-store/revisions/'..revision..'.source-'..
                            source_index..'.duplicate-'..number..'.original.cre'
                        local file=assert(io.open(path,'rb'),'SOURCE_GATE_TEMPLATE_MISSING')
                        entries[auxiliary.resource]=file:read('*a');assert(file:close())
                    end
                end
                if endpoint.kind=='guarded-template' then
                    local source_index
                    for index,source in ipairs(guarded.manifest.sources) do if source.id==id then source_index=index end end
                    local path=DATA..'campaign-store/revisions/'..revision..'.source-'..assert(source_index)..'.original.cre'
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
                    local path=DATA..'campaign-store/revisions/'..revision..'.source-'..source_index..'.patched.'..extension
                    local file=assert(io.open(path,'rb'),'SOURCE_GATE_SCRIPT_MISSING')
                    pristine.scripts[revision]=pristine.scripts[revision] or {}
                    pristine.scripts[revision][id]=file:read('*a'); assert(file:close())
                    if endpoint.resources then
                        local routes={}
                        for number,resource in ipairs(endpoint.resources) do
                            local kind=assert(resource:match('^(%a+):'))
                            assert(kind=='dlg' or kind=='bcs','SOURCE_GATE_SCRIPT_KIND_INVALID')
                            local route=DATA..'campaign-store/revisions/'..revision..'.source-'..
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
                    local prefix=DATA..'campaign-store/revisions/'..revision..'.source-'..source_index
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
                        local file=assert(io.open(DATA..area..".pristine.are","rb"),"SOURCE_GATE_PRISTINE_MISSING")
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
                    local prefix=DATA.."campaign-store/revisions/"..revision..".source-"..source_index
                    local creature={}
                    for _,kind in ipairs({'original','removed'}) do
                        local file=assert(io.open(prefix..'.'..kind..'.cre','rb'),"SOURCE_GATE_CREATURE_MISSING")
                        creature[kind]=file:read('*a'); assert(file:close())
                    end
                    pristine.creatures[revision]=pristine.creatures[revision] or {}
                    pristine.creatures[revision][id]=creature
                end
                -- Check every item before this revision's checks can suppress one.
                local supported,reason=Native.supported(_G,endpoint.payload)
                assert(supported,reason)
            end
            return guarded
        end
        if #revisions > 0 then
            local Inventory, Cursed
            root.SourceGate = Gate.new(_G, {
                revisions = revisions, load = load_revision, pristine = pristine,
                build = module("override/FLPRGateAsm.lua"), compile = module("override/FLPRCampaignPolicy.lua"),
                Script = module("override/FLPRScript.lua"),
                inventory = function()
                    Inventory = Inventory or module("override/FLPRInventoryAsm.lua")
                    return Inventory
                end,
                cursed = function()
                    Cursed = Cursed or module("override/FLPRCur.lua", Site)
                    return Cursed
                end,
                report = record,
            })
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
        -- The revision New Game chose for the campaign being started.
        next = function() return resolve(root.NewGameIntent and root.NewGameIntent.revision) end,
        native = function(value)
            local has_shop=false
            for _,target in pairs(value.targets) do if target.kind=='store' then has_shop=true end end
            if has_shop then
                if not root.Shop then root.Shop=module('override/FLPRShop.lua', Site).install(_G) end
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
            else
                record("campaign", reason)
                -- A failed capture was undone (state 0); AcceptCharacter asks the player.
                warn_stopped()
            end
        end,
    })
    -- EEex_LuaAction's NORMAL return holds the speaker before EscapeArea until
    -- its randomized inventory is ready. DONE resumes the original dialogue.
    function FLPR_Transfer(speaker, target_id, campaign)
        -- Dialogue parsing strips spaces and colons from Lua chunks. Authored
        -- Mode 2 inventory rewards pass their numeric row, a BG1 row its
        -- campaign too (1); expand it here.
        if type(target_id)=='number' then
            target_id='original-target:'..(campaign==1 and 'bg1' or 'bg2')..':mode2:target:'..tostring(target_id)
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
    root.Disabled = nil
end)
if not ok then
    root.Disabled = tostring(failure)
    record("start", root.Disabled)
end

function root.Poll()
    if root.Spell and root.Spell.last_error and root.Host and not root.Disabled then
        root.Disabled='CAMPAIGN_SPELL_CALLBACK_FAILED:'..root.Spell.last_error
        root.Host:_disable(root.Disabled)
    end
    if root.Disabled then warn_stopped()
    elseif root.Host then root.Host:poll() end
    -- Confirms randomisation once, when the captured adventure first reaches the world.
    if root.Announce and root.Host and (root.Host.status == "running" or root.Host.status == "complete") then
        if root.Storage:get(root.Core.G.seed) == root.Announce then Infinity_DisplayString(text("ready")) end
        root.Announce = nil
    end
    if root.Held then
        if root.Host and root.Host.status == "complete" and not root.Disabled then
            root.Held:poll(EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_worldTime.m_gameTime)
        else root.Held:destroy() end
    end
    return ""
end

-- Leaves no trace of an attempted capture, so the engine starts this new
-- adventure unrandomised.
local function forget_new_game()
    root.NewGameIntent, root.Announce, root.Warned = nil, nil, nil
    if root.Held then root.Held:destroy(); root.Held = nil end
    if root.Host then root.Host:destroy() end
    if root.SourceGate then root.SourceGate:reset() end
end

-- The campaign a New Game or character import starts, as the engine selected
-- it rather than as a UI's tables describe it: the CAMPAIGN.2DA row (EET's
-- campaigns, Siege of Dragonspear in BG:EE), else Black Pits, the tutorial,
-- Throne of Bhaal (BG2:EE) or the game's main campaign. GetCampaign() is 3
-- exactly when CInfGame has a campaign set (m_bCampaignEnabled).
local function starting_campaign()
    if startEngine:GetCampaign() == 3 then
        local name = EngineGlobals.g_pBaldurChitin.m_pObjectGame.m_sCurrentCampaign.m_pchData:get()
        assert(type(name) == "string" and name ~= "", "CAMPAIGN_NAME_UNAVAILABLE")
        return name:upper()
    end
    if game:GetMissionPackInfoTBP() then return "BP" end
    if game:GetMissionPackInfo() then return "TUT" end
    if game:GetMissionPackInfo25() then return "TOB" end
    return "MAIN"
end

-- The applied configuration names a revision for each campaign start it
-- randomises; "main" names the campaign the engine starts without a campaign
-- row (BG1 in BG:EE, SoA in BG2:EE). A pointer written before campaign starts
-- were configurable holds one revision, which was always a BG1 start.
local function applied_revision(campaign)
    local pointer = module(DATA .. "next.lua")
    if type(pointer) == "string" then pointer = { schema = "flir-next-v2", starts = { BG1 = pointer } } end
    assert(type(pointer) == "table" and pointer.schema == "flir-next-v2" and type(pointer.starts) == "table" and
        (pointer.main == nil or type(pointer.main) == "string"), "NEXT_POINTER_INVALID")
    if campaign == "MAIN" then campaign = pointer.main end
    local revision = campaign and pointer.starts[campaign]
    assert(revision == nil or (type(revision) == "string" and #revision == 64 and not revision:find("[^0-9a-f]")),
        "NEXT_POINTER_INVALID")
    return revision, campaign
end

-- The mode the engine started character creation in (CScreenCreateChar's
-- engine state: 1 New Game, 7 character import, 4 pre-generating a character,
-- 5 party creation, 3 multiplayer); nil where it cannot be read.
local function chargen_mode()
    local ok, mode = pcall(function() return createCharScreen:GetEngineState() end)
    if ok then return mode end
end

-- Every New Game and character import asks before it starts unrandomised: when
-- the runtime cannot run, or the applied configuration has nothing for the
-- campaign being started (for example Black Pits).
local function begin_new_game(engine_call)
    forget_new_game()
    local refusal, intent
    if root.Disabled then refusal = failure_message(root.Disabled)
    else
        local ok, result, campaign, started = pcall(function()
            local started = starting_campaign()
            local revision, campaign = applied_revision(started)
            return revision, campaign, started
        end)
        if not ok then
            record("new-game", result)
            refusal = "setup_failed"
        elseif not result then refusal = "other_campaign"
        else intent = { campaign = campaign, revision = result, started = started } end
    end
    -- The started campaign's source checks exist before the engine reads its
    -- new-game template.
    if intent and root.SourceGate and root.SourceGate:guards(intent.revision) then
        local built, reason = pcall(root.SourceGate.ensure, root.SourceGate, intent.revision)
        if not built then
            record("new-game", reason)
            refusal, intent = failure_message(reason), nil
        end
    end
    if refusal then return FLPRUI_Ask(refusal, engine_call) end
    engine_call()
    intent.chargen = chargen_mode()
    root.NewGameIntent = intent
end

-- Character creation that another start began: the engine reports another
-- campaign or another creation mode. A cancelled start leaves its intent
-- behind; the tutorial, pre-generating a character and party creation reach
-- character acceptance without a hooked start and must not capture it.
local function begun_elsewhere(intent)
    local ok, started = pcall(starting_campaign)
    local mode = chargen_mode()
    return (ok and started ~= intent.started) or (mode ~= nil and intent.chargen ~= nil and mode ~= intent.chargen)
end

function root.NewGame(...)
    local arguments = { ... }
    begin_new_game(function() startEngine:OnNewGameButtonClick((unpack or table.unpack)(arguments)) end)
end

-- Importing a character (BG2:EE's SoA start, BG:EE's Siege of Dragonspear)
-- starts a new game too and ends in character acceptance.
function root.ImportCharacter(...)
    local arguments = { ... }
    begin_new_game(function() startEngine:OnImportCharacterButtonClick((unpack or table.unpack)(arguments)) end)
end

function root.AcceptCharacter(difficulty)
    if root.NewGameIntent and begun_elsewhere(root.NewGameIntent) then forget_new_game() end
    if not root.NewGameIntent then return createCharScreen:AcceptCharacter(difficulty) end
    local captured, reason
    if root.Disabled or not root.Host then
        reason = root.Disabled or "HOST_MISSING"
    else
        captured, reason = root.Host:capture_new("confirmed-character-accept")
    end
    if captured then
        root.Announce = root.Storage:get(root.Core.G.seed)
        return createCharScreen:AcceptCharacter(difficulty)
    end
    -- capture_new undid its GLOBAL writes; also disarm whatever the source gate
    -- prepared for it. Cancel keeps character creation open for a retry.
    if root.SourceGate then root.SourceGate:reset() end
    return FLPRUI_Ask(failure_message(reason), function()
        forget_new_game()
        createCharScreen:AcceptCharacter(difficulty)
    end)
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
    root.NewGameIntent, root.Warned = nil, nil
    if root.Held then root.Held:destroy(); root.Held = nil end
    if root.Host then root.Host:destroy() end
end

-- The heartbeat also runs when the runtime could not start, so a randomised
-- save loaded in that state still tells the player.
root.OnMenusLoaded = function()
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
