"use strict";

const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  "appShell", "gameContext", "savedSelect", "newButton", "presetButton", "profileName",
  "saveButton", "copyButton", "validateButton", "prepareButton", "status", "statusSpinner",
  "statusText", "statusElapsed", "validation",
  "applySafetyNote", "applyDevelopmentNote", "itemsTab",
  "enemiesTab", "searchLabel", "librarySearch", "searchSummary", "libraryResults",
  "recipientFilterRow", "moreResults", "dirtyMark", "itemCount", "targetCount", "selectedItems",
  "selectedTargets", "shopPercent", "workspacePath", "newDialog", "newForm", "newName", "cancelNew",
  "presetDialog", "presetForm", "presetSelect", "presetItems", "presetTargets", "presetScrolls",
  "presetKangaxx", "presetFlailHeads", "presetTotscTome", "presetMoreSpellShield",
  "presetCursedDisguise", "presetLossMode", "presetLossPercent", "presetUnrandomizedPercent",
  "presetNote", "presetError", "presetFields", "presetProgress", "presetElapsed", "createPreset",
  "cancelPreset", "tierDialog", "tierForm", "tierTitle", "tierPrompt",
  "tierSelect", "cancelTier", "selectionFilter", "partFilter", "tierFilter"
].map((id) => [id, $(id)]));

// The parts of the game, in game order: the names the editor shows and the full titles.
const PART_NAMES = { bg1: "BG1", sod: "SoD", soa: "BG2", wk: "Watcher's Keep", tob: "ToB" };
const PART_TITLES = {
  bg1: "Baldur's Gate", sod: "Siege of Dragonspear", soa: "Shadows of Amn",
  wk: "Watcher's Keep", tob: "Throne of Bhaal"
};
// The part a preset table's tiers stand for where a record's place does not tell it.
const TABLE_PARTS = { bg1: "bg1", sod: "sod", bg2: "soa" };
const TABLE_ORDER = ["bg1", "sod", "bg2"];
// Item types by the category number in their ITM file.
const ITEM_TYPES = ["Miscellaneous", "Amulet", "Armor", "Belt", "Boots", "Arrows", "Bracers or gauntlets",
  "Headgear", "Key", "Potion", "Ring", "Scroll", "Shield", "Food", "Bullets", "Bow", "Dagger", "Mace", "Sling",
  "Small sword", "Large sword", "Hammer", "Morning star", "Flail", "Darts", "Axe", "Quarterstaff", "Crossbow",
  "Hand-to-hand weapon", "Spear", "Halberd", "Bolts", "Cloak or robe", "Gold", "Gem", "Wand"];

let profile = null;
let plan = null;
let selectedItems = new Map();
let selectedTargets = new Map();
let targetInputDrafts = new Map();
let basePriceDrafts = new Map();
let shopPercentDraft = "120";
let sourcePages = new Map();
let sourceChoiceTouched = new Set();
let expandedItems = new Set();
let activeTab = "items";
let dirty = false;
let busy = false;
let verified = false;
let applyEnabled = false;
let generation = 0;
let searchTimer = null;
let recommendedPreset = null;
let presetTemplates = null;
let pendingItemTiers = new Map();
let sourceParts = new Map();
let filterTimer = null;
const searches = {
  items: { query: "", records: [], total: 0, next: null, sequence: 0, loading: false, searched: false },
  enemies: { query: "", records: [], total: 0, next: null, sequence: 0, loading: false, searched: false }
};

function node(tag, className, content) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (content !== undefined) result.textContent = String(content);
  return result;
}

function setStatus(message, error = false) {
  ui.statusText.textContent = message;
  ui.status.classList.toggle("error", error);
}

// Seconds since `started`, for a running action and its result.
function seconds(started) {
  return Math.floor((Date.now() - started) / 1000);
}

// While an action runs, the status line shows a spinner and the seconds so far.
let statusTimer = null;
let actionStarted = 0;
function setBusy(value) {
  busy = value;
  ui.appShell.inert = value;
  ui.appShell.setAttribute("aria-busy", String(value));
  ui.status.classList.toggle("working", value);
  ui.statusSpinner.hidden = !value;
  clearInterval(statusTimer);
  statusTimer = null;
  ui.statusElapsed.textContent = "";
  if (!value) return;
  actionStarted = Date.now();
  statusTimer = setInterval(() => { ui.statusElapsed.textContent = `${seconds(actionStarted)} s`; }, 1000);
}

// " (12 s)" after an action that took a noticeable time.
function took() {
  const elapsed = seconds(actionStarted);
  return elapsed >= 2 ? ` (${elapsed} s)` : "";
}

// A message as a sentence, so that more sentences can follow it.
function sentence(text) {
  return /[.!?]$/.test(text) ? text : text + ".";
}

// A check's item copies and recipient slots, and what their balance means;
// a check that could not build the campaign has none.
function counts(result) {
  const units = result.reward_units, slots = result.target_capacity;
  if (result.valid !== true || !Number.isInteger(units) || !Number.isInteger(slots)) return "";
  const balance = units > slots ? " Some recipients receive more than one item."
    : units < slots ? " Some recipient slots stay empty." : "";
  return ` ${units.toLocaleString()} item copies for ${slots.toLocaleString()} recipient slots.${balance}`;
}

function changed() {
  dirty = true;
  verified = false;
  ui.prepareButton.disabled = true;
  ui.dirtyMark.hidden = false;
  ui.validation.hidden = true;
  setStatus("Unsaved changes. Save this draft when you are ready.");
}

function askDiscard() {
  return !dirty || window.confirm("Discard unsaved changes to this configuration?");
}

function recordError(error) {
  setStatus(error instanceof Error ? error.message : String(error), true);
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { cache: "no-store", ...options });
  } catch (error) {
    throw new Error("Cannot reach the local editor. Check that it is still running.");
  }
  let body;
  try {
    body = await response.json();
  } catch (error) {
    throw new Error(`The local editor returned an unreadable response (${response.status}).`);
  }
  if (!response.ok || !body || body.ok !== true) {
    throw new Error(body && typeof body.error === "string" ? body.error : `Request failed (${response.status}).`);
  }
  return body.result;
}

function post(path, value) {
  return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
}

function acceptState(state, message, preserveSourceChoices = false) {
  if (!state || !state.profile || !state.plan) throw new Error("The local editor returned an incomplete configuration.");
  generation += 1;
  profile = state.profile;
  plan = state.plan;
  selectedItems = new Map((state.items || []).map((row) => [row.resref, row]));
  selectedTargets = new Map((state.targets || []).map((row) => [row.id, row]));
  targetInputDrafts = new Map();
  basePriceDrafts = new Map();
  shopPercentDraft = String(profile.shop_pricing?.percent ?? 120);
  sourcePages = new Map();
  sourceParts = new Map(Object.entries(state.source_parts || {}));
  if (!preserveSourceChoices) sourceChoiceTouched = new Set();
  expandedItems = new Set();
  pendingItemTiers = new Map();
  dirty = false;
  verified = false;
  applyEnabled = state.apply_enabled === true;
  ui.prepareButton.disabled = true;
  ui.prepareButton.textContent = applyEnabled ? "Apply for next game" : "Prepare test settings";
  ui.applySafetyNote.hidden = !applyEnabled;
  ui.applyDevelopmentNote.hidden = !applyEnabled;
  ui.dirtyMark.hidden = true;
  ui.validation.hidden = true;
  ui.profileName.value = profile.name;
  ui.shopPercent.value = shopPercentDraft;
  ui.workspacePath.textContent = state.workspace ? `Draft workspace: ${state.workspace}` : "";
  const catalogue = state.catalog || {};
  const count = Number.isInteger(catalogue.item_count) ? `${catalogue.item_count.toLocaleString()} scanned items` : "Scanned items";
  ui.gameContext.textContent = [catalogue.game_name, catalogue.language, count].filter(Boolean).join(" · ");
  renderSaved(state.saved || []);
  renderSelected();
  renderResults();
  setStatus(message);
}

function renderSaved(saved) {
  ui.savedSelect.replaceChildren();
  ui.savedSelect.add(new Option("Choose a saved configuration", ""));
  for (const row of saved) ui.savedSelect.add(new Option(row.name, row.id));
  ui.savedSelect.value = saved.some((row) => row.id === profile.id) ? profile.id : "";
}

function restoreSavedSelection() {
  ui.savedSelect.value = [...ui.savedSelect.options].some((option) => option.value === profile?.id)
    ? profile.id : "";
}

function sourceEntry(item) {
  return plan.sources.find((row) => row.item === item);
}

function getItem(item) {
  return selectedItems.get(item) || { resref: item, name: item };
}

function getTarget(id) {
  return selectedTargets.get(id) || { id, label: id };
}

function shopPricing() {
  if (!profile.shop_pricing) profile.shop_pricing = { percent: 120, base_prices: {} };
  return profile.shop_pricing;
}

function tierRules() {
  return plan?.randomization || null;
}

function namedTiers() {
  const rules = tierRules();
  if (!rules) return [];
  const names = new Set([
    ...Object.values(rules.sources || {}).map((row) => row.tier),
    ...Object.values(rules.targets || {}).flat()
  ]);
  return [...names].filter((value) => typeof value === "string" && value).sort(compareTiers);
}

function itemType(category) {
  return Number.isInteger(category) ? ITEM_TYPES[category] || `Category ${category}` : "";
}

// "bg2:12" is tier 12 of the BG2 tables.
function tierCode(tier) {
  return tier.slice(tier.indexOf(":") + 1);
}

function tierTable(tier) {
  const colon = tier.indexOf(":");
  return colon > 0 ? tier.slice(0, colon) : "";
}

function tierName(tier) {
  const table = PART_NAMES[TABLE_PARTS[tierTable(tier)]];
  return `${table ? `${table} ` : ""}tier ${tierCode(tier)}`;
}

// Tables in game order, then their tiers, numbered ones by number.
function compareTiers(a, b) {
  const rank = (tier) => {
    const index = TABLE_ORDER.indexOf(tierTable(tier));
    return index < 0 ? TABLE_ORDER.length : index;
  };
  return rank(a) - rank(b) || a.localeCompare(b, undefined, { numeric: true });
}

function partNames(parts) {
  return (parts || []).map((part) => PART_NAMES[part] || part).join(", ");
}

// One chip per game part and tier of [parts, tier] pairs (a chosen record, or a
// reward slot), with how many pairs it stands for. A record whose place does not
// tell its part takes the part of its tier's table.
function chipsOf(pairs) {
  const chips = new Map();
  for (const [parts, slotTier] of pairs) {
    const tier = typeof slotTier === "string" ? slotTier : "";
    const known = parts && parts.length ? parts : [TABLE_PARTS[tierTable(tier)] || ""];
    for (const part of known) {
      const key = `${part}\u0000${tier}`;
      const chip = chips.get(key) || { part, tier, count: 0 };
      chip.count += 1;
      chips.set(key, chip);
    }
  }
  const order = Object.keys(PART_NAMES);
  const rank = (part) => (order.includes(part) ? order.indexOf(part) : order.length);
  return [...chips.values()].filter((chip) => chip.part || chip.tier)
    .sort((a, b) => rank(a.part) - rank(b.part) || compareTiers(a.tier, b.tier));
}

function itemChips(item) {
  const entry = sourceEntry(item);
  const ids = [...(entry?.source_ids || []), ...(entry?.registration_ids || [])];
  const rules = tierRules();
  return chipsOf(ids.map((id) => [sourceParts.get(id), rules?.sources?.[id]?.tier]));
}

function slotPairs(target) {
  const parts = getTarget(target.id).parts;
  const slots = tierRules()?.targets?.[target.id];
  const tiers = Array.isArray(slots) && slots.length ? slots
    : Array(Number.isInteger(target.capacity) && target.capacity > 0 ? target.capacity : 1).fill("");
  return tiers.map((tier) => [parts, tier]);
}

function chipLabel(chip, counted) {
  const part = chip.part ? PART_NAMES[chip.part] || chip.part : "Place unknown";
  return `${part}${chip.tier ? ` · tier ${tierCode(chip.tier)}` : ""}${counted && chip.count > 1 ? ` ×${chip.count}` : ""}`;
}

function chipList(chips, counted) {
  const list = node("span", "chips");
  const shown = chips.length > 6 ? chips.slice(0, 5) : chips;
  for (const chip of shown) {
    const element = node("span", `chip${chip.part ? ` part-${chip.part}` : ""}`, chipLabel(chip, counted));
    element.title = [PART_TITLES[chip.part], chip.tier ? tierName(chip.tier) : ""].filter(Boolean).join(", ");
    list.append(element);
  }
  if (shown.length < chips.length) list.append(node("span", "chip", `+${chips.length - shown.length} more`));
  return list;
}

function chipText(chips) {
  return chips.map((chip) => [chip.part, PART_NAMES[chip.part], PART_TITLES[chip.part], chip.tier,
    chip.tier ? `tier ${tierCode(chip.tier)}` : ""].filter(Boolean).join(" ")).join(" ");
}

// The filter over this configuration's lists; null when it shows everything.
function listFilter() {
  const terms = ui.selectionFilter.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const part = ui.partFilter.value;
  const tier = ui.tierFilter.value;
  return terms.length || part || tier ? { terms, part, tier } : null;
}

function chipMatches(filter, chip) {
  return (!filter.part || chip.part === filter.part) && (!filter.tier || chip.tier === filter.tier);
}

function textMatches(filter, text) {
  const haystack = text.toLowerCase();
  return filter.terms.every((term) => haystack.includes(term));
}

// Keeps the filter's choices to the game parts and tiers this configuration has
// (and the current choice), rebuilding them only when that set changes.
function updateFilterOptions(chips) {
  const order = Object.keys(PART_NAMES);
  const parts = new Set(chips.map((chip) => chip.part).filter(Boolean));
  const tiers = new Set(chips.map((chip) => chip.tier).filter(Boolean));
  if (ui.partFilter.value) parts.add(ui.partFilter.value);
  if (ui.tierFilter.value) tiers.add(ui.tierFilter.value);
  const partList = [...parts].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const tierList = [...tiers].sort(compareTiers);
  const signature = JSON.stringify([partList, tierList]);
  if (ui.partFilter.dataset.signature === signature) return;
  ui.partFilter.dataset.signature = signature;
  const [part, tier] = [ui.partFilter.value, ui.tierFilter.value];
  ui.partFilter.replaceChildren(new Option("All parts", ""), ...partList.map((name) => new Option(PART_NAMES[name] || name, name)));
  ui.tierFilter.replaceChildren(new Option("All tiers", ""), ...tierList.map((name) => new Option(tierName(name), name)));
  ui.partFilter.value = part;
  ui.tierFilter.value = tier;
}

function chooseTier(prompt) {
  const tiers = namedTiers();
  if (!tiers.length) {
    setStatus("This preset has no existing tier to choose. Check its original pool settings.", true);
    return Promise.resolve(null);
  }
  ui.tierPrompt.textContent = prompt;
  ui.tierSelect.replaceChildren(new Option("Choose a tier", ""));
  for (const tier of tiers) ui.tierSelect.add(new Option(tier, tier));
  ui.tierSelect.value = "";
  ui.tierDialog.returnValue = "";
  return new Promise((resolve) => {
    ui.tierDialog.addEventListener("close", () => {
      resolve(tiers.includes(ui.tierDialog.returnValue) ? ui.tierDialog.returnValue : null);
    }, { once: true });
    ui.tierDialog.showModal();
    ui.tierSelect.focus();
  });
}

function pruneTargetExclusions(id, maximum = 0) {
  const rules = tierRules();
  if (!rules) return;
  for (const entry of Object.values(rules.sources || {})) {
    entry.exclude_targets = (entry.exclude_targets || []).filter((choice) =>
      choice.id !== id || (maximum > 0 && choice.ordinal <= maximum));
  }
}

function pruneSourceRules(ids) {
  const rules = tierRules();
  for (const id of ids) {
    if (rules) delete rules.sources[id];
    if (rules?.row_filters) delete rules.row_filters.sources[id];
    if (plan.replacements) delete plan.replacements[id];
  }
  // A source that no longer moves keeps Item Randomiser's second copies of its item.
  if (plan.duplicates) {
    plan.duplicates = plan.duplicates.filter((row) => !ids.includes(row.source_id));
    if (!plan.duplicates.length) delete plan.duplicates;
  }
}

function wholeNumber(value, min, max = Number.MAX_SAFE_INTEGER) {
  return /^\d+$/.test(value) && Number.isSafeInteger(Number(value))
    && Number(value) >= min && Number(value) <= max;
}

function renderSelected() {
  if (!profile || !plan) return;
  ui.selectedItems.replaceChildren();
  ui.selectedTargets.replaceChildren();
  const filter = listFilter();
  const itemRows = profile.items.map((item) => ({ item, data: getItem(item), chips: itemChips(item) }));
  const targetRows = plan.targets.map((target) => {
    const pairs = slotPairs(target);
    return { target, data: getTarget(target.id), pairs, chips: chipsOf(pairs) };
  });
  updateFilterOptions([...itemRows, ...targetRows].flatMap((row) => row.chips));
  const shownItems = itemRows.filter(({ item, data, chips }) => !filter
    || (textMatches(filter, [data.name, item, itemType(data.category), chipText(chips)].join(" "))
      && (!filter.part && !filter.tier || chips.some((chip) => chipMatches(filter, chip)))));
  const itemTotal = `${profile.items.length} ${profile.items.length === 1 ? "item" : "items"}`;
  ui.itemCount.textContent = filter ? `${shownItems.length} of ${itemTotal}` : itemTotal;
  const slots = plan.targets.reduce((sum, row) => sum + (Number.isInteger(row.capacity) ? row.capacity : 0), 0);
  const slotTotal = `${slots} ${slots === 1 ? "slot" : "slots"}`;
  // A reward slot matches when its recipient matches the words and its own game
  // part and tier match the choices.
  let shownSlots = 0;
  const shownTargets = targetRows.filter(({ target, data, pairs, chips }) => {
    if (!filter) return true;
    if (!textMatches(filter, [data.label, target.id, data.kind_label || data.kind, chipText(chips)].join(" "))) return false;
    if (!filter.part && !filter.tier) {
      shownSlots += Number.isInteger(target.capacity) ? target.capacity : 0;
      return true;
    }
    const matching = pairs.filter((pair) => chipsOf([pair]).some((chip) => chipMatches(filter, chip))).length;
    shownSlots += matching;
    return matching > 0;
  });
  ui.targetCount.textContent = filter ? `${shownSlots} of ${slotTotal}` : slotTotal;
  if (!profile.items.length) ui.selectedItems.append(node("p", "empty", "No items selected. Search the library to add one."));
  else if (!shownItems.length) ui.selectedItems.append(node("p", "empty", "No selected item matches this search."));
  for (const { item, data, chips } of shownItems) {
    const details = node("details", "chosen-row");
    details.open = expandedItems.has(item);
    const summary = node("summary");
    const title = node("span", "chosen-main");
    title.append(node("span", "record-name", data.name || item),
      node("span", "record-detail", [item, itemType(data.category)].filter(Boolean).join(" · ")));
    if (chips.length) title.append(chipList(chips, false));
    summary.append(title);
    details.append(summary);
    const body = node("div", "chosen-body");
    const basePrice = node("div", "base-price-row");
    const priceLabel = node("label", "", "Optional base price (gold)");
    const priceInput = node("input");
    priceInput.type = "number";
    priceInput.min = "0";
    priceInput.max = "4294967295";
    priceInput.step = "1";
    priceInput.inputMode = "numeric";
    priceInput.id = `base-price-${item}`;
    priceInput.placeholder = Number.isInteger(data.price) ? String(data.price) : "Catalog price";
    priceInput.value = basePriceDrafts.has(item) ? basePriceDrafts.get(item)
      : String(profile.shop_pricing?.base_prices?.[item] ?? "");
    priceLabel.htmlFor = priceInput.id;
    priceInput.setAttribute("aria-label", `Optional base price in gold for ${data.name || item}`);
    priceInput.addEventListener("input", () => {
      changed();
      if (priceInput.value === "") {
        if (profile.shop_pricing) delete profile.shop_pricing.base_prices[item];
        basePriceDrafts.delete(item);
      } else if (priceInput.validity.valid && wholeNumber(priceInput.value, 0, 4294967295)) {
        shopPricing().base_prices[item] = Number(priceInput.value);
        basePriceDrafts.delete(item);
      } else {
        basePriceDrafts.set(item, priceInput.value);
      }
    });
    const priceHint = node("span", "small-note", Number.isInteger(data.price)
      ? `Catalog base price: ${data.price.toLocaleString()} gold. Leave blank to use it.`
      : "Leave blank to use the catalog base price.");
    basePrice.append(priceLabel, priceInput, priceHint);
    body.append(basePrice);
    renderSourcePicker(item, body);
    const actions = node("div", "chosen-actions");
    const remove = node("button", "text-button remove", "Remove item");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${data.name || item}`);
    remove.addEventListener("click", () => removeItem(item));
    actions.append(remove);
    body.append(actions);
    details.append(body);
    details.addEventListener("toggle", () => {
      if (details.open) {
        expandedItems.add(item);
        if (!sourcePages.has(item)) fetchSources(item);
      } else {
        expandedItems.delete(item);
      }
    });
    ui.selectedItems.append(details);
  }
  if (!plan.targets.length) ui.selectedTargets.append(node("p", "empty", "No recipients selected. Search Recipients to add reward slots."));
  else if (!shownTargets.length) ui.selectedTargets.append(node("p", "empty", "No recipient matches this search."));
  for (const { target, data, chips } of shownTargets) {
    const row = node("div", "chosen-row");
    const heading = node("div", "result-row");
    const main = node("div", "result-main");
    main.append(node("div", "record-name", data.label || target.id),
      node("div", "record-detail", `${target.id}${data.kind_label || data.kind ? ` · ${data.kind_label || data.kind}` : ""}`));
    if (chips.length) main.append(chipList(chips, true));
    const remove = node("button", "text-button remove", "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${data.label || target.id}`);
    remove.addEventListener("click", () => removeTarget(target.id));
    heading.append(main, remove);
    const capacity = node("div", "capacity-row");
    const label = node("label", "", "Reward slots");
    const input = node("input");
    input.type = "number";
    input.min = "1";
    input.max = "4096";
    input.step = "1";
    input.value = targetInputDrafts.has(target.id) ? targetInputDrafts.get(target.id) : String(target.capacity);
    input.id = `capacity-${plan.targets.indexOf(target)}`;
    label.htmlFor = input.id;
    input.setAttribute("aria-label", `Reward slots for ${data.label || target.id}`);
    input.addEventListener("input", () => {
      targetInputDrafts.set(target.id, input.value);
    });
    input.addEventListener("change", async () => {
      if (!input.validity.valid || !wholeNumber(input.value, 1, 4096)) {
        input.setCustomValidity("Enter a whole number from 1 to 4096.");
        input.reportValidity();
        input.setCustomValidity("");
        setStatus("Reward slots must be a whole number from 1 to 4096.", true);
        return;
      }
      const next = Number(input.value);
      if (next === target.capacity) { targetInputDrafts.delete(target.id); return; }
      const rules = tierRules();
      if (rules) {
        const slots = rules.targets?.[target.id];
        if (!Array.isArray(slots) || slots.length !== target.capacity) {
          input.value = String(target.capacity);
          targetInputDrafts.delete(target.id);
          setStatus("This recipient's preset tiers need review before changing its slots.", true);
          return;
        }
        if (next > target.capacity) {
          setBusy(true);
          let tier;
          try { tier = await chooseTier(`Choose a tier for ${next - target.capacity} new reward slot${next - target.capacity === 1 ? "" : "s"} on ${data.label || target.id}.`); }
          finally { setBusy(false); }
          if (!tier) {
            input.value = String(target.capacity);
            targetInputDrafts.delete(target.id);
            return;
          }
          slots.push(...Array(next - target.capacity).fill(tier));
        } else {
          slots.length = next;
          pruneTargetExclusions(target.id, next);
        }
      }
      target.capacity = next;
      const registration = (plan.target_registrations || []).find((row) => row.id === target.id);
      if (registration) registration.capacity = next;
      targetInputDrafts.delete(target.id);
      changed();
      renderSelected();
    });
    capacity.append(label, input);
    row.append(heading, capacity);
    ui.selectedTargets.append(row);
  }
}

function renderSourcePicker(item, body) {
  const entry = sourceEntry(item);
  const ids = [...(entry?.source_ids || []), ...(entry?.registration_ids || [])];
  const authoredRegistrations = (plan.registrations || []).filter((row) =>
    row.item === item && row.adapter_id === "original-source-v1" && ids.includes(row.id));
  const selection = ids.length
    ? `${ids.length} ${ids.length === 1 ? "original" : "originals"} selected`
    : "No originals selected. Choose one or more records below.";
  body.append(node("p", `source-selection${ids.length ? "" : " missing"}`, selection));
  body.append(node("p", "source-note", "Each checked record moves its existing copies; a shop record moves its full finite stock. Check this draft to confirm current runtime support."));
  for (const registration of authoredRegistrations) {
    const authored = registration.authored || {};
    const label = node("label", "source-option");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.checked = true;
    checkbox.setAttribute("aria-label", `Keep original source ${authored.source || registration.id}`);
    checkbox.addEventListener("change", () => setSourceSelected(item, registration.id, checkbox.checked));
    const text = node("span");
    const where = partNames(sourceParts.get(registration.id));
    text.append(node("span", "record-name", authored.source || registration.id),
      node("span", "record-detail", `${authored.campaign || "Original"} · tier ${authored.tier || "?"}${where ? ` · ${where}` : ""} · ${authored.source_ref || registration.id}`));
    label.append(checkbox, text);
    body.append(label);
  }
  const page = sourcePages.get(item);
  if (!page || (page.loading && !page.records.length)) {
    body.append(node("p", "small-note", "Loading original records…"));
    return;
  }
  if (page.error) {
    body.append(node("p", "source-selection missing", page.error));
    const retry = node("button", "button secondary", "Retry originals");
    retry.type = "button";
    retry.addEventListener("click", () => fetchSources(item));
    body.append(retry);
    return;
  }
  if (!page.records.length) {
    body.append(node("p", "small-note", authoredRegistrations.length
      ? "No additional physical records were found for this item."
      : "No original record was found for this item. This draft cannot be used yet."));
    return;
  }
  const group = node("fieldset", "source-list");
  group.append(node("legend", "", "Exact originals"));
  for (const source of page.records) {
    const label = node("label", "source-option");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.value = source.id;
    checkbox.checked = ids.includes(source.id);
    checkbox.addEventListener("change", () => setSourceSelected(item, source.id, checkbox.checked));
    const text = node("span");
    const stock = source.concerns?.includes("infinite-stock")
      ? "infinite stock"
      : `${source.quantity.toLocaleString()} ${source.quantity === 1 ? "copy" : "copies"}`;
    const where = partNames(source.parts);
    text.append(node("span", "record-name", source.label || source.id),
      node("span", "record-detail", `${source.id}${source.kind_label || source.kind ? ` · ${source.kind_label || source.kind}` : ""}${where ? ` · ${where}` : ""} · ${stock}`));
    if (source.note) text.append(node("span", "record-detail", source.note));
    if (source.concerns && source.concerns.length) text.append(node("span", "record-detail", `Scan notes: ${source.concerns.join(", ")}`));
    label.append(checkbox, text);
    group.append(label);
  }
  body.append(group);
  if (page.loading) body.append(node("p", "small-note", "Loading more originals…"));
  if (page.next !== null && page.next !== undefined) {
    const more = node("button", "button secondary", "Show more originals");
    more.type = "button";
    more.addEventListener("click", () => fetchSources(item, true));
    body.append(more);
  }
}

async function fetchSources(item, append = false) {
  if (!profile || !profile.items.includes(item)) return;
  const previous = sourcePages.get(item);
  if (previous && previous.loading) return;
  const page = append && previous ? previous : { records: [], next: 0, total: 0 };
  const offset = append ? page.next : 0;
  if (offset === null || offset === undefined) return;
  page.loading = true;
  page.error = "";
  sourcePages.set(item, page);
  renderSelected();
  const currentGeneration = generation;
  try {
    const result = await request(`/api/sources?item=${encodeURIComponent(item)}&offset=${offset}`);
    if (generation !== currentGeneration || !profile.items.includes(item) || sourcePages.get(item) !== page) return;
    page.records = append ? [...page.records, ...(result.records || [])] : (result.records || []);
    for (const record of result.records || []) {
      if (typeof record.id === "string" && Array.isArray(record.parts)) sourceParts.set(record.id, record.parts);
    }
    page.next = result.next_offset;
    page.total = result.total;
    const entry = sourceEntry(item);
    if (!tierRules() && !append && !sourceChoiceTouched.has(item) && result.total === 1 && page.records.length === 1
      && (!entry || (entry.policy === "relocate"
        && Array.isArray(entry.source_ids) && entry.source_ids.length === 0
        && (!Array.isArray(entry.registration_ids) || entry.registration_ids.length === 0)))
      && typeof page.records[0].id === "string" && page.records[0].id) {
      setSourceSelected(item, page.records[0].id, true, true);
    }
  } catch (error) {
    if (generation !== currentGeneration || !profile.items.includes(item) || sourcePages.get(item) !== page) return;
    page.error = error.message;
    recordError(error);
  } finally {
    if (generation === currentGeneration && profile.items.includes(item) && sourcePages.get(item) === page) {
      page.loading = false;
      renderSelected();
    }
  }
}

async function setSourceSelected(item, id, selected, automatic = false) {
  if (!profile.items.includes(item)) return;
  if (!automatic) sourceChoiceTouched.add(item);
  let entry = sourceEntry(item);
  if (entry && entry.policy !== "relocate") {
    setStatus("This item's source policy needs review before selecting an original record.", true);
    renderSelected();
    return;
  }
  if (!entry) {
    entry = { item, policy: "relocate", source_ids: [], registration_ids: [] };
  }
  const registration = sourcePages.get(item)?.records.find((row) => row.id === id)?.registration
    || plan.registrations.find((row) => row.id === id);
  const field = registration ? "registration_ids" : "source_ids";
  const ids = Array.isArray(entry[field]) ? entry[field] : [];
  if (selected === ids.includes(id)) return;
  const rules = tierRules();
  if (selected && rules) {
    let tier = pendingItemTiers.get(item);
    if (!tier) {
      setBusy(true);
      try { tier = await chooseTier(`Choose the tier for this original record of ${getItem(item).name || item}.`); }
      finally { setBusy(false); }
    }
    if (!tier) { renderSelected(); return; }
    rules.sources[id] = { tier, loss_exempt: false, exclude_targets: [] };
    if (rules.row_filters) rules.row_filters.sources[id] = { chance: 100, group: "" };
  }
  if (!sourceEntry(item)) plan.sources.push(entry);
  entry[field] = selected ? [...ids, id] : ids.filter((choice) => choice !== id);
  if (!selected) pruneSourceRules([id]);
  if (registration) {
    plan.registrations = plan.registrations.filter((row) => row.id !== id);
    if (selected) plan.registrations.push(registration);
  }
  changed();
  renderSelected();
}

async function addItem(row) {
  if (profile.items.includes(row.resref)) return;
  if (tierRules()) {
    setBusy(true);
    let tier;
    try { tier = await chooseTier(`Choose the tier for new item ${row.name || row.resref}. Its selected original records will use this tier.`); }
    finally { setBusy(false); }
    if (!tier) return;
    pendingItemTiers.set(row.resref, tier);
  }
  profile.items.push(row.resref);
  profile.items.sort();
  if (!sourceEntry(row.resref)) plan.sources.push({ item: row.resref, policy: "relocate", source_ids: [], registration_ids: [] });
  plan.sources.sort((a, b) => a.item.localeCompare(b.item));
  selectedItems.set(row.resref, row);
  expandedItems.add(row.resref);
  changed();
  renderSelected();
  renderResults();
  fetchSources(row.resref);
}

function removeItem(item) {
  const entry = sourceEntry(item);
  if (entry) pruneSourceRules([...(entry.source_ids || []), ...(entry.registration_ids || [])]);
  profile.items = profile.items.filter((id) => id !== item);
  if (profile.shop_pricing) delete profile.shop_pricing.base_prices[item];
  basePriceDrafts.delete(item);
  plan.sources = plan.sources.filter((row) => row.item !== item);
  plan.registrations = plan.registrations.filter((row) => row.item !== item);
  sourcePages.delete(item);
  expandedItems.delete(item);
  pendingItemTiers.delete(item);
  changed();
  renderSelected();
  renderResults();
}

async function addTarget(row) {
  if (plan.targets.some((entry) => entry.id === row.id)) return;
  const rules = tierRules();
  if (rules) {
    setBusy(true);
    let tier;
    try { tier = await chooseTier(`Choose the tier for new recipient ${row.label || row.id}.`); }
    finally { setBusy(false); }
    if (!tier) return;
    rules.targets[row.id] = [tier];
    if (rules.row_filters) rules.row_filters.targets[row.id] = { chance: 100, group: "" };
  }
  plan.targets.push({ id: row.id, capacity: 1, optional: false });
  plan.targets.sort((a, b) => a.id.localeCompare(b.id));
  selectedTargets.set(row.id, row);
  changed();
  renderSelected();
  renderResults();
}

function removeTarget(id) {
  const rules = tierRules();
  if (rules) {
    delete rules.targets[id];
    if (rules.row_filters) delete rules.row_filters.targets[id];
    pruneTargetExclusions(id);
  }
  plan.targets = plan.targets.filter((row) => row.id !== id);
  if (plan.target_registrations) {
    plan.target_registrations = plan.target_registrations.filter((row) => row.id !== id);
  }
  targetInputDrafts.delete(id);
  changed();
  renderSelected();
  renderResults();
}

function selectedRecipientKinds() {
  return [...ui.recipientFilterRow.querySelectorAll("input:checked")].map((input) => input.value);
}

function renderResults() {
  const search = searches[activeTab];
  ui.libraryResults.replaceChildren();
  ui.moreResults.hidden = search.next === null || search.next === undefined || search.loading;
  if (activeTab === "enemies" && !selectedRecipientKinds().length) {
    ui.searchSummary.textContent = "Select at least one recipient type.";
    ui.moreResults.hidden = true;
    return;
  }
  if (!search.query.trim()) {
    ui.searchSummary.textContent = activeTab === "items" ? "Search to browse the scanned items." : "Search creatures, containers and shops in the world scan.";
    return;
  }
  if (search.loading && !search.records.length) ui.searchSummary.textContent = "Searching the scan…";
  else if (search.searched) ui.searchSummary.textContent = `${search.total.toLocaleString()} ${activeTab === "items" ? "items" : "recipients"} found`;
  if (search.searched && !search.records.length) {
    ui.libraryResults.append(node("p", "empty", "No matches. Try a different name or resource ID."));
    return;
  }
  for (const row of search.records) {
    const isItem = activeTab === "items";
    const id = isItem ? row.resref : row.id;
    const chosen = isItem ? profile.items.includes(id) : plan.targets.some((target) => target.id === id);
    const result = node("div", "result-row");
    const main = node("div", "result-main");
    const kind = isItem ? itemType(row.category) : [row.kind_label || row.kind, partNames(row.parts)].filter(Boolean).join(" · ");
    main.append(node("div", "record-name", isItem ? row.name || id : row.label || id),
      node("div", "record-detail", `${id}${kind ? ` · ${kind}` : ""}`));
    if (row.note) main.append(node("div", "record-detail", row.note));
    const button = node("button", "button secondary", chosen ? "Added" : "Add");
    button.type = "button";
    button.disabled = chosen;
    button.setAttribute("aria-label", `${chosen ? "Already added" : "Add"} ${isItem ? row.name || id : row.label || id}`);
    button.addEventListener("click", () => isItem ? addItem(row) : addTarget(row));
    result.append(main, button);
    ui.libraryResults.append(result);
  }
}

async function searchPage(tab, append = false) {
  const search = searches[tab];
  const query = search.query.trim();
  if (!query) return;
  const kinds = tab === "enemies" ? selectedRecipientKinds() : [];
  if (tab === "enemies" && !kinds.length) return;
  if (append && (search.next === null || search.loading)) return;
  const offset = append ? search.next : 0;
  const sequence = ++search.sequence;
  search.loading = true;
  if (!append) {
    search.records = [];
    search.searched = false;
  }
  if (activeTab === tab) renderResults();
  try {
    const params = `q=${encodeURIComponent(query)}&offset=${offset}`;
    let result;
    if (tab === "items") {
      result = await request(`/api/items?${params}`);
    } else {
      // Each kind has its own complete search page, so mixed filters retain
      // accurate totals and cannot hide matches behind excluded result types.
      const pages = await Promise.all((kinds.length === 3 ? ["all"] : kinds)
        .map((kind) => request(`/api/targets?${params}&kind=${kind}`)));
      result = {
        records: pages.flatMap((page) => page.records),
        total: pages.reduce((total, page) => total + page.total, 0),
        next_offset: pages.find((page) => page.next_offset !== null)?.next_offset ?? null
      };
    }
    if (search.sequence !== sequence || search.query.trim() !== query) return;
    search.records = append ? [...search.records, ...(result.items || result.records || [])] : (result.items || result.records || []);
    search.total = result.total;
    search.next = result.next_offset;
    search.searched = true;
  } catch (error) {
    if (search.sequence !== sequence) return;
    recordError(error);
  } finally {
    if (search.sequence === sequence) {
      search.loading = false;
      if (activeTab === tab) renderResults();
    }
  }
}

function setTab(tab) {
  activeTab = tab;
  ui.itemsTab.setAttribute("aria-selected", String(tab === "items"));
  ui.enemiesTab.setAttribute("aria-selected", String(tab === "enemies"));
  ui.libraryResults.setAttribute("aria-labelledby", tab === "items" ? "itemsTab" : "enemiesTab");
  ui.recipientFilterRow.hidden = tab !== "enemies";
  ui.searchLabel.textContent = tab === "items" ? "Search items by name or resource ID" : "Search recipients by name, area, game part or resource ID";
  ui.librarySearch.placeholder = tab === "items" ? "Enter an item name or ID" : "Enter a name, area, game part or ID";
  ui.librarySearch.value = searches[tab].query;
  renderResults();
  if (searches[tab].query.trim() && !searches[tab].searched && !searches[tab].loading) searchPage(tab);
}

function localInputValid() {
  if (!ui.profileName.value.trim()) {
    ui.profileName.setCustomValidity("Enter a configuration name.");
    ui.profileName.reportValidity();
    ui.profileName.setCustomValidity("");
    return false;
  }
  if (!ui.shopPercent.validity.valid || !wholeNumber(ui.shopPercent.value, 0, 4294967295)) {
    ui.shopPercent.setCustomValidity("Enter a whole percentage from 0 to 4,294,967,295.");
    ui.shopPercent.reportValidity();
    ui.shopPercent.setCustomValidity("");
    setStatus("The shop price multiplier must be a whole percentage from 0 to 4,294,967,295.", true);
    return false;
  }
  const invalidPrice = [...ui.selectedItems.querySelectorAll(".base-price-row input")]
    .find((input) => input.value !== "" && (!input.validity.valid || !wholeNumber(input.value, 0, 4294967295)));
  if (invalidPrice) {
    invalidPrice.closest("details").open = true;
    invalidPrice.setCustomValidity("Enter a whole gold amount from 0 to 4,294,967,295, or leave it blank.");
    invalidPrice.reportValidity();
    invalidPrice.setCustomValidity("");
    setStatus("An item base price must be a whole gold amount, or blank to use its catalog price.", true);
    return false;
  }
  const invalid = [...ui.selectedTargets.querySelectorAll(".capacity-row input")]
    .find((input) => !input.validity.valid || !/^\d+$/.test(input.value));
  if (invalid) {
    invalid.setCustomValidity("Enter a whole number from 1 to 4096.");
    invalid.reportValidity();
    invalid.setCustomValidity("");
    setStatus("Reward slots must be a whole number from 1 to 4096.", true);
    return false;
  }
  return true;
}

async function perform(action, busyMessage) {
  if (busy) return;
  setBusy(true);
  setStatus(busyMessage);
  try { await action(); } catch (error) { recordError(error); }
  finally { setBusy(false); }
}

// The preset dialog covers the page's status line, so it shows its own progress
// and stays open until the draft is built.
let presetTimer = null;
// The preset dialog's box for each original option.
function presetOptionBoxes() {
  return {
    scrolls: ui.presetScrolls, kangaxx: ui.presetKangaxx, flail_heads: ui.presetFlailHeads,
    totsc_tome: ui.presetTotscTome, more_spell_shield: ui.presetMoreSpellShield,
    cursed_disguise: ui.presetCursedDisguise
  };
}

function setPresetBusy(value) {
  ui.presetFields.disabled = value;
  ui.presetForm.setAttribute("aria-busy", String(value));
  ui.createPreset.textContent = value ? "Creating…" : "Create preset draft";
  ui.presetProgress.hidden = !value;
  clearInterval(presetTimer);
  presetTimer = null;
  if (!value) return;
  const started = Date.now();
  const showElapsed = () => { ui.presetElapsed.textContent = `${seconds(started)} s`; };
  showElapsed();
  presetTimer = setInterval(showElapsed, 1000);
}

ui.librarySearch.addEventListener("input", () => {
  const search = searches[activeTab];
  search.query = ui.librarySearch.value;
  search.records = [];
  search.next = null;
  search.searched = false;
  search.sequence += 1;
  clearTimeout(searchTimer);
  renderResults();
  if (search.query.trim()) {
    const tab = activeTab;
    searchTimer = setTimeout(() => searchPage(tab), 240);
  }
});
ui.itemsTab.addEventListener("click", () => setTab("items"));
ui.enemiesTab.addEventListener("click", () => setTab("enemies"));
ui.recipientFilterRow.addEventListener("change", () => {
  clearTimeout(searchTimer);
  const search = searches.enemies;
  search.records = [];
  search.total = 0;
  search.next = null;
  search.loading = false;
  search.searched = false;
  search.sequence += 1;
  if (activeTab === "enemies") renderResults();
  if (search.query.trim()) searchPage("enemies");
});
ui.moreResults.addEventListener("click", () => searchPage(activeTab, true));
ui.selectionFilter.addEventListener("input", () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(renderSelected, 150);
});
ui.partFilter.addEventListener("change", renderSelected);
ui.tierFilter.addEventListener("change", renderSelected);
ui.profileName.addEventListener("input", () => { if (profile) { profile.name = ui.profileName.value; changed(); } });
ui.shopPercent.addEventListener("input", () => {
  if (!profile) return;
  shopPercentDraft = ui.shopPercent.value;
  changed();
  if (ui.shopPercent.validity.valid && wholeNumber(shopPercentDraft, 0, 4294967295)) {
    shopPricing().percent = Number(shopPercentDraft);
  }
});
ui.savedSelect.addEventListener("change", () => {
  const id = ui.savedSelect.value;
  if (!id || id === profile?.id || !askDiscard()) { restoreSavedSelection(); return; }
  perform(async () => {
    try { acceptState(await post("/api/load", { id }), "Saved configuration loaded."); }
    catch (error) { restoreSavedSelection(); throw error; }
  }, "Loading configuration…");
});
ui.newButton.addEventListener("click", () => {
  if (!askDiscard()) return;
  ui.newName.value = "";
  ui.newDialog.showModal();
  ui.newName.focus();
});
ui.cancelNew.addEventListener("click", () => ui.newDialog.close());
ui.newForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = ui.newName.value.trim();
  if (!name) { ui.newName.reportValidity(); return; }
  ui.newDialog.close();
  perform(async () => acceptState(await post("/api/new", { name }), "New draft created."), "Creating draft…");
});
ui.presetButton.addEventListener("click", () => {
  perform(async () => {
    if (!presetTemplates) presetTemplates = await request("/api/templates");
    if (!recommendedPreset) recommendedPreset = await request("/api/recommended");
    ui.presetSelect.replaceChildren(new Option("Choose a preset", ""));
    for (const row of presetTemplates) ui.presetSelect.add(new Option(row.name, row.id));
    // Opens on the recommended default; every choice stays editable.
    const preset = recommendedPreset;
    ui.presetSelect.value = preset.id;
    ui.presetItems.value = preset.items;
    ui.presetTargets.value = preset.targets;
    ui.presetScrolls.checked = preset.options.scrolls;
    ui.presetKangaxx.checked = preset.options.kangaxx;
    ui.presetFlailHeads.checked = preset.options.flail_heads;
    ui.presetTotscTome.checked = preset.options.totsc_tome;
    ui.presetMoreSpellShield.checked = preset.options.more_spell_shield;
    ui.presetCursedDisguise.checked = preset.options.cursed_disguise;
    ui.presetLossMode.value = preset.options.loss_mode;
    ui.presetLossPercent.value = String(preset.options.loss_percent);
    ui.presetLossPercent.disabled = preset.options.loss_mode !== "some";
    ui.presetUnrandomizedPercent.value = String(preset.options.unrandomized_percent);
    // Options this game cannot apply are not offered; the note says why.
    const unavailable = preset.unavailable || {};
    for (const [option, box] of Object.entries(presetOptionBoxes())) box.disabled = option in unavailable;
    ui.presetNote.textContent = Object.values(unavailable).map((reason) => reason + ".").join(" ");
    ui.presetNote.hidden = !ui.presetNote.textContent;
    ui.presetError.hidden = true;
    ui.presetDialog.showModal();
    ui.presetSelect.focus();
    setStatus("Choose preset settings to create a new draft.");
  }, "Loading presets…");
});
ui.presetLossMode.addEventListener("change", () => {
  ui.presetLossPercent.disabled = ui.presetLossMode.value !== "some";
  if (ui.presetLossPercent.disabled) ui.presetLossPercent.value = "0";
});
ui.cancelPreset.addEventListener("click", () => ui.presetDialog.close());
ui.presetDialog.addEventListener("cancel", (event) => {
  if (ui.presetFields.disabled) event.preventDefault();
});
ui.cancelTier.addEventListener("click", () => ui.tierDialog.close(""));
ui.tierForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (ui.tierForm.reportValidity()) ui.tierDialog.close(ui.tierSelect.value);
});
ui.presetForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!ui.presetForm.reportValidity() || !askDiscard()) return;
  const wholePercent = (input) => /^\d+$/.test(input.value) && Number(input.value) <= 100;
  if (!wholePercent(ui.presetUnrandomizedPercent) ||
      (ui.presetLossMode.value === "some" && !wholePercent(ui.presetLossPercent))) {
    ui.presetError.textContent = "Percentages must be whole numbers from 0 to 100.";
    ui.presetError.hidden = false;
    return;
  }
  const options = {
    scrolls: ui.presetScrolls.checked,
    kangaxx: ui.presetKangaxx.checked,
    flail_heads: ui.presetFlailHeads.checked,
    totsc_tome: ui.presetTotscTome.checked,
    more_spell_shield: ui.presetMoreSpellShield.checked,
    cursed_disguise: ui.presetCursedDisguise.checked,
    loss_mode: ui.presetLossMode.value,
    loss_percent: ui.presetLossMode.value === "some" ? Number(ui.presetLossPercent.value) : 0,
    unrandomized_percent: Number(ui.presetUnrandomizedPercent.value)
  };
  if (busy) return;
  ui.presetError.hidden = true;
  setPresetBusy(true);
  perform(async () => {
    try {
      const state = await post("/api/template", {
        id: ui.presetSelect.value, items: ui.presetItems.value,
        targets: ui.presetTargets.value, options
      });
      const leftOut = (state.left_out || []).map((reason) => " " + reason + ".").join("");
      const absentMods = (state.absent_mods || []).map((reason) => " " + reason + ".").join("");
      const notUsed = (state.not_used || []).map((reason) => " " + reason + ".").join("");
      acceptState(state, `Preset draft created${took()}. Check configuration for current runtime support.` +
        (leftOut ? " Rewards that stay as installed:" + leftOut : "") + absentMods + notUsed);
      ui.presetDialog.close();
    } catch (error) {
      ui.presetError.textContent = error instanceof Error ? error.message : String(error);
      ui.presetError.hidden = false;
      throw error;
    }
  }, "Creating preset draft…").finally(() => setPresetBusy(false));
});
ui.saveButton.addEventListener("click", () => {
  if (!localInputValid()) return;
  profile.name = ui.profileName.value.trim();
  perform(async () => acceptState(await post("/api/save", { profile, plan, copy: false }), "Draft saved.", true), "Saving draft…");
});
ui.copyButton.addEventListener("click", () => {
  if (!localInputValid()) return;
  profile.name = ui.profileName.value.trim();
  perform(async () => acceptState(await post("/api/save", { profile, plan, copy: true }), "Copy saved as a new configuration.", true), "Saving copy…");
});
ui.validateButton.addEventListener("click", () => {
  if (!localInputValid()) return;
  profile.name = ui.profileName.value.trim();
  verified = false;
  ui.prepareButton.disabled = true;
  ui.validation.hidden = true;
  perform(async () => {
    const result = await post("/api/validate", { profile, plan });
    verified = result.valid === true && result.runtime_supported === true;
    ui.prepareButton.disabled = !verified;
    ui.validation.hidden = false;
    ui.validation.className = `validation ${result.valid && result.runtime_supported ? "supported" : result.valid ? "warning" : "error"}`;
    ui.validation.textContent = sentence(result.message || "Configuration checked.") + counts(result);
    setStatus(`Configuration check complete${took()}. This check does not change the game.`);
  }, "Checking the configuration against your game…");
});
ui.prepareButton.addEventListener("click", () => {
  if (!verified || !localInputValid()) return;
  perform(async () => {
    if (applyEnabled) {
      const result = await post("/api/apply", { profile, plan });
      // The result box holds the details; the status line only says it is done.
      acceptState(await request("/api/state"), `Applied to the game${took()}.`);
      ui.validation.hidden = false;
      ui.validation.className = "validation supported";
      ui.validation.textContent = result.message || "Configuration applied for the next game.";
      return;
    }
    const result = await post("/api/prepare", { profile, plan });
    const location = result.store ? ` Saved in ${result.store}.` : "";
    ui.validation.hidden = false;
    ui.validation.className = "validation supported";
    ui.validation.textContent = `${result.message || "Test settings prepared."}${location}`;
    setStatus(`Test settings prepared outside the game${took()}.`);
  }, applyEnabled ? "Applying: building the randomised settings for each start and writing them into the game…"
    : "Preparing test settings…");
});
window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });

perform(async () => acceptState(await request("/api/state"), "Ready. Search the library to edit this draft."), "Loading configuration…");
