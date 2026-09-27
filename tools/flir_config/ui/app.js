"use strict";

const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  "appShell", "gameContext", "savedSelect", "loadButton", "newButton", "profileName",
  "saveButton", "copyButton", "validateButton", "prepareButton", "status", "validation", "itemsTab",
  "enemiesTab", "searchLabel", "librarySearch", "searchSummary", "libraryResults",
  "moreResults", "dirtyMark", "itemCount", "targetCount", "selectedItems",
  "selectedTargets", "workspacePath", "newDialog", "newForm", "newName", "cancelNew"
].map((id) => [id, $(id)]));

let profile = null;
let plan = null;
let selectedItems = new Map();
let selectedTargets = new Map();
let targetInputDrafts = new Map();
let sourcePages = new Map();
let expandedItems = new Set();
let activeTab = "items";
let dirty = false;
let busy = false;
let verified = false;
let generation = 0;
let searchTimer = null;
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
  ui.status.textContent = message;
  ui.status.classList.toggle("error", error);
}

function setBusy(value) {
  busy = value;
  ui.appShell.inert = value;
  ui.appShell.setAttribute("aria-busy", String(value));
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

function acceptState(state, message) {
  if (!state || !state.profile || !state.plan) throw new Error("The local editor returned an incomplete configuration.");
  generation += 1;
  profile = state.profile;
  plan = state.plan;
  selectedItems = new Map((state.items || []).map((row) => [row.resref, row]));
  selectedTargets = new Map((state.targets || []).map((row) => [row.id, row]));
  targetInputDrafts = new Map();
  sourcePages = new Map();
  expandedItems = new Set();
  dirty = false;
  verified = false;
  ui.prepareButton.disabled = true;
  ui.dirtyMark.hidden = true;
  ui.validation.hidden = true;
  ui.profileName.value = profile.name;
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

function sourceEntry(item) {
  return plan.sources.find((row) => row.item === item);
}

function getItem(item) {
  return selectedItems.get(item) || { resref: item, name: item };
}

function getTarget(id) {
  return selectedTargets.get(id) || { id, label: id };
}

function renderSelected() {
  if (!profile || !plan) return;
  ui.selectedItems.replaceChildren();
  ui.selectedTargets.replaceChildren();
  ui.itemCount.textContent = `${profile.items.length} ${profile.items.length === 1 ? "item" : "items"}`;
  const slots = plan.targets.reduce((sum, row) => sum + (Number.isInteger(row.capacity) ? row.capacity : 0), 0);
  ui.targetCount.textContent = `${slots} ${slots === 1 ? "slot" : "slots"}`;
  if (!profile.items.length) ui.selectedItems.append(node("p", "empty", "No items selected. Search the library to add one."));
  for (const item of profile.items) {
    const data = getItem(item);
    const details = node("details", "chosen-row");
    details.open = expandedItems.has(item);
    const summary = node("summary");
    const title = node("span", "chosen-main");
    title.append(node("span", "record-name", data.name || item), node("span", "record-detail", item));
    summary.append(title);
    details.append(summary);
    const body = node("div", "chosen-body");
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
  for (const target of plan.targets) {
    const data = getTarget(target.id);
    const row = node("div", "chosen-row");
    const heading = node("div", "result-row");
    const main = node("div", "result-main");
    main.append(node("div", "record-name", data.label || target.id),
      node("div", "record-detail", `${target.id}${data.kind ? ` · ${data.kind}` : ""}`));
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
      changed();
      if (input.validity.valid && /^\d+$/.test(input.value)) {
        target.capacity = Number(input.value);
        targetInputDrafts.delete(target.id);
        const total = plan.targets.reduce((sum, entry) => sum + entry.capacity, 0);
        ui.targetCount.textContent = `${total} ${total === 1 ? "slot" : "slots"}`;
      } else {
        targetInputDrafts.set(target.id, input.value);
      }
    });
    input.addEventListener("change", () => {
      if (!input.validity.valid || !/^\d+$/.test(input.value)) {
        input.setCustomValidity("Enter a whole number from 1 to 4096.");
        input.reportValidity();
        input.setCustomValidity("");
        setStatus("Reward slots must be a whole number from 1 to 4096.", true);
      }
    });
    capacity.append(label, input);
    row.append(heading, capacity);
    ui.selectedTargets.append(row);
  }
}

function renderSourcePicker(item, body) {
  const entry = sourceEntry(item);
  const ids = entry && Array.isArray(entry.source_ids) ? entry.source_ids : [];
  let selection;
  if (!ids.length) selection = "No original chosen. Choose one exact record below.";
  else if (ids.length === 1) selection = `Chosen original: ${ids[0]}`;
  else selection = `${ids.length} saved originals: ${ids.join(", ")}. Choosing a radio will replace them with one record.`;
  body.append(node("p", `source-selection${ids.length ? "" : " missing"}`, selection));
  body.append(node("p", "source-note", "The record and its notes come from the world scan. Check this draft to confirm current runtime support."));
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
    body.append(node("p", "small-note", "No original record was found for this item. This draft cannot be used yet."));
    return;
  }
  const group = node("fieldset", "source-list");
  group.append(node("legend", "", "Exact original"));
  for (const source of page.records) {
    const label = node("label", "source-option");
    const radio = node("input");
    radio.type = "radio";
    radio.name = `source-${item}`;
    radio.value = source.id;
    radio.checked = ids.length === 1 && ids[0] === source.id;
    radio.addEventListener("change", () => chooseSource(item, source.id));
    const text = node("span");
    text.append(node("span", "record-name", source.label || source.id),
      node("span", "record-detail", `${source.id}${source.kind ? ` · ${source.kind}` : ""}`));
    if (source.note) text.append(node("span", "record-detail", source.note));
    if (source.concerns && source.concerns.length) text.append(node("span", "record-detail", `Scan notes: ${source.concerns.join(", ")}`));
    label.append(radio, text);
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
    page.next = result.next_offset;
    page.total = result.total;
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

function chooseSource(item, id) {
  if (!profile.items.includes(item)) return;
  let entry = sourceEntry(item);
  if (!entry) {
    entry = { item, policy: "relocate", source_ids: [], registration_ids: [] };
    plan.sources.push(entry);
  }
  entry.source_ids = [id];
  changed();
  renderSelected();
}

function addItem(row) {
  if (profile.items.includes(row.resref)) return;
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
  profile.items = profile.items.filter((id) => id !== item);
  plan.sources = plan.sources.filter((row) => row.item !== item);
  sourcePages.delete(item);
  expandedItems.delete(item);
  changed();
  renderSelected();
  renderResults();
}

function addTarget(row) {
  if (plan.targets.some((entry) => entry.id === row.id)) return;
  plan.targets.push({ id: row.id, capacity: 1, optional: false });
  plan.targets.sort((a, b) => a.id.localeCompare(b.id));
  selectedTargets.set(row.id, row);
  changed();
  renderSelected();
  renderResults();
}

function removeTarget(id) {
  plan.targets = plan.targets.filter((row) => row.id !== id);
  targetInputDrafts.delete(id);
  changed();
  renderSelected();
  renderResults();
}

function renderResults() {
  const search = searches[activeTab];
  ui.libraryResults.replaceChildren();
  ui.moreResults.hidden = search.next === null || search.next === undefined || search.loading;
  if (!search.query.trim()) {
    ui.searchSummary.textContent = activeTab === "items" ? "Search to browse the scanned items." : "Search enemies and NPCs in the world scan.";
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
    main.append(node("div", "record-name", isItem ? row.name || id : row.label || id),
      node("div", "record-detail", `${id}${isItem && row.category !== undefined ? ` · category ${row.category}` : row.kind ? ` · ${row.kind}` : ""}`));
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
    const path = tab === "items" ? "/api/items" : "/api/targets";
    const result = await request(`${path}?q=${encodeURIComponent(query)}&offset=${offset}`);
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
  ui.searchLabel.textContent = tab === "items" ? "Search items by name or resource ID" : "Search recipients by name or resource ID";
  ui.librarySearch.placeholder = tab === "items" ? "Enter an item name or ID" : "Enter an enemy or NPC name or ID";
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
  const invalid = [...ui.selectedTargets.querySelectorAll("input")]
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
ui.moreResults.addEventListener("click", () => searchPage(activeTab, true));
ui.profileName.addEventListener("input", () => { if (profile) { profile.name = ui.profileName.value; changed(); } });
ui.loadButton.addEventListener("click", () => {
  const id = ui.savedSelect.value;
  if (!id) { setStatus("Choose a saved configuration to load.", true); return; }
  if (!askDiscard()) return;
  perform(async () => acceptState(await post("/api/load", { id }), "Saved configuration loaded."), "Loading configuration…");
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
ui.saveButton.addEventListener("click", () => {
  if (!localInputValid()) return;
  profile.name = ui.profileName.value.trim();
  perform(async () => acceptState(await post("/api/save", { profile, plan, copy: false }), "Draft saved."), "Saving draft…");
});
ui.copyButton.addEventListener("click", () => {
  if (!localInputValid()) return;
  profile.name = ui.profileName.value.trim();
  perform(async () => acceptState(await post("/api/save", { profile, plan, copy: true }), "Copy saved as a new configuration."), "Saving copy…");
});
ui.validateButton.addEventListener("click", () => {
  if (!localInputValid()) return;
  profile.name = ui.profileName.value.trim();
  verified = false;
  ui.prepareButton.disabled = true;
  perform(async () => {
    const result = await post("/api/validate", { profile, plan });
    verified = result.valid === true && result.runtime_supported === true;
    ui.prepareButton.disabled = !verified;
    ui.validation.hidden = false;
    ui.validation.className = `validation ${result.valid && result.runtime_supported ? "supported" : result.valid ? "warning" : "error"}`;
    const counts = Number.isInteger(result.reward_units) && Number.isInteger(result.target_capacity)
      ? ` ${result.reward_units} reward units, ${result.target_capacity} target slots.` : "";
    ui.validation.textContent = `${result.message || "Configuration checked."}${counts}`;
    setStatus("Configuration check complete. This check does not change the game.");
  }, "Checking configuration…");
});
ui.prepareButton.addEventListener("click", () => {
  if (!verified || !localInputValid()) return;
  perform(async () => {
    const result = await post("/api/prepare", { profile, plan });
    const location = result.store ? ` Saved in ${result.store}.` : "";
    ui.validation.hidden = false;
    ui.validation.className = "validation supported";
    ui.validation.textContent = `${result.message || "Test settings prepared."}${location}`;
    setStatus("Test settings prepared outside the game.");
  }, "Preparing test settings…");
});
window.addEventListener("beforeunload", (event) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });

perform(async () => acceptState(await request("/api/state"), "Ready. Search the library to edit this draft."), "Loading configuration…");
