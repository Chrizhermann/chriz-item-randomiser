--@nowatchdog
-- v4 hand-over (2026-08-26): bug-denied items ONLY (12). Proven EEex primitives only.
-- Run via eeex-remote.ps1 with the game loaded on save "yaga dead" (000000476), world screen.
local S, G = EEex_GameState_SetGlobalInt, EEex_GameState_GetGlobalInt

-- 1) Vongoethe watchpoint deliveries must still be live (do NOT touch them)
if G("fl10t13") ~= 12 or G("fl10t16") ~= 10 or G("fl12t15") ~= 6 then
  return {aborted="vongoethe", v={G("fl10t13"),G("fl10t16"),G("fl12t15")}}
end
-- 2) delivered-sanity: tokens of items the mod already "delivered" (and lost) must read -1
for _,t in ipairs({"fl10t06","fl13t06","fl5t08","fl7t12","fl7t03"}) do
  if G(t) ~= -1 then return {aborted="sanity", tok=t, val=G(t)} end
end
-- 3) guards: exact pending values expected, else the wrong save is loaded
local guards = {
  {"fl10t17",20},{"fl6t11",4},{"fl10t01",1},{"fl10t15",18},
  {"fl4t07",18},{"fl11t09",13},{"fl12t08",3},
}
for _,g in ipairs(guards) do
  if G(g[1]) ~= g[2] then return {aborted="guard", tok=g[1], expect=g[2], got=G(g[1])} end
end
local party = {}
for i=0,5 do
  local s = EEex_Sprite_GetInPortrait(i)
  if s then party[#party+1] = s end
end
if #party == 0 then return {error="no party"} end
for _,g in ipairs(guards) do S(g[1], -1) end
-- charges read from the effective ITMs (override): only halb10 [0,2,0] and wa2helm [1,0,0] are charged
local items = {
  {"halb10",0,2,0},{"sw2h21",0,0,0},{"ax1h10",0,0,0},{"ax1h16",0,0,0},
  {"sw1h66",0,0,0},{"dagg23",0,0,0},{"halb04",0,0,0},{"ring46",0,0,0},
  {"compon08",0,0,0},{"helm07",0,0,0},{"halb05",0,0,0},{"wa2helm",1,0,0},
}
local placed = {}
for n,it in ipairs(items) do
  local m = party[((n-1) % #party) + 1]
  local p = m.m_pos
  local loc = "[" .. math.floor(p.x) .. "." .. math.floor(p.y) .. "]"
  EEex_Action_QueueResponseStringOnAIBase(
    ('CreateItem("%s",%d,%d,%d)'):format(it[1],it[2],it[3],it[4]), m)
  EEex_Action_QueueResponseStringOnAIBase(
    ('DropItem("%s",%s)'):format(it[1],loc), m)
  placed[#placed+1] = it[1] .. "@" .. loc
end
local after = {}
for _,g in ipairs(guards) do after[g[1]] = G(g[1]) end
return {ok=true, n=#items, placed=placed, after=after,
        area=party[1].m_pArea.m_resref:get()}
