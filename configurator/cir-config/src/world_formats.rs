//! Physical inventories from BG:EE/BG2:EE CRE/ARE/STO V1 resources (the
//! reference's `world_formats.py`).
//!
//! These are structural observations, not proof that a template spawns, a
//! record becomes an obtainable item, or that every scripted grant was found.
//! Byte offsets refer to the supplied resource; embedded CRE offsets are
//! rebased to their enclosing ARE. Item indexes refer to their own table.
//! Layouts: https://gibberlings3.github.io/iesdp/file_formats/ie_formats/
//! (cre_v1, are_v1, sto_v1). Conflicting active storage and overlapping
//! embedded byte spans stay unsupported ambiguity instead of being guessed.

use crate::error::{Error, Result};
use crate::json::{Dict, Value};
use crate::py::{hex, latin1, u16_le, u32_le};

fn header(data: &[u8], signature: &[u8; 8], size: usize) -> Result<()> {
    let text = std::str::from_utf8(signature).expect("ascii signature");
    if data.len() < size {
        return Err(Error::scan(format!("Truncated {} header.", text[..4].trim())));
    }
    if &data[..8] != signature {
        return Err(Error::scan(format!("Unsupported world resource format; expected {text}.")));
    }
    Ok(())
}

fn slice(data: &[u8], offset: usize, size: usize) -> &[u8] {
    let start = offset.min(data.len());
    let end = offset.saturating_add(size).min(data.len());
    &data[start..end]
}

/// A non-TLK object name (Latin-1, up to the first NUL).
pub fn text(data: &[u8], offset: usize, size: usize) -> String {
    let raw = slice(data, offset, size);
    let raw = match raw.iter().position(|&byte| byte == 0) {
        Some(index) => &raw[..index],
        None => raw,
    };
    latin1(raw)
}

/// The reference's `_reference`: an eight-byte resref, lowercased.
pub fn reference(data: &[u8], offset: usize, optional: bool, embedded_marker: bool) -> Result<String> {
    let raw = slice(data, offset, 8);
    let (body, padding) = match raw.iter().position(|&byte| byte == 0) {
        Some(index) => (&raw[..index], Some(&raw[index + 1..])),
        None => (raw, None),
    };
    if padding.is_some_and(|padding| padding.iter().any(|&byte| byte != 0)) {
        return Err(Error::scan("A world resource reference has nonzero bytes after its terminator."));
    }
    if optional && body.is_empty() {
        return Ok(String::new());
    }
    if !body.is_ascii() {
        return Err(Error::scan("A world resource reference is not ASCII."));
    }
    let name = std::str::from_utf8(body).expect("ascii");
    let checked = if embedded_marker && name.starts_with('*') { &name[1..] } else { name };
    if checked.is_empty() || checked.bytes().any(|byte| !(33..=126).contains(&byte) || b"/\\:*?\"<>|".contains(&byte))
    {
        return Err(Error::scan("Invalid world resource reference."));
    }
    Ok(name.to_ascii_lowercase())
}

/// Bounds-check nonempty sections and reject overlapping byte ownership.
struct Sections {
    size: u64,
    header_size: u64,
    spans: Vec<(u64, u64, String)>,
}

impl Sections {
    fn new(data: &[u8], header_size: u64) -> Self {
        Sections { size: data.len() as u64, header_size, spans: Vec::new() }
    }

    fn add(&mut self, offset: u64, count: u64, stride: u64, label: &str) -> Result<()> {
        if count == 0 {
            // Empty arrays have no bytes: writers may leave unused offsets.
            return Ok(());
        }
        let length = count * stride;
        if offset < self.header_size {
            return Err(Error::scan(format!("{label} overlaps its resource header.")));
        }
        if offset > self.size || length > self.size - offset {
            return Err(Error::scan(format!("Invalid or truncated {label}.")));
        }
        self.spans.push((offset, offset + length, label.to_string()));
        Ok(())
    }

    fn verify(&self) -> Result<()> {
        let mut spans: Vec<&(u64, u64, String)> = self.spans.iter().collect();
        spans.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)).then_with(|| a.2.as_bytes().cmp(b.2.as_bytes())));
        let (mut previous_end, mut previous_label) = (0u64, "");
        for (start, end, label) in spans {
            if *start < previous_end {
                return Err(Error::scan(format!("Overlapping world sections: {previous_label} and {label}.")));
            }
            previous_end = *end;
            previous_label = label;
        }
        Ok(())
    }
}

fn items(data: &[u8], offset: u64, count: u64, stride: u64, allow_invalid: bool) -> Result<Vec<Dict>> {
    // An all-NUL reference is an observed empty physical record, not an item.
    // Keep its payload and index: later slot/container references still use
    // the original table. Malformed names fail by default; creature discovery
    // may keep their exact eight bytes for quarantine without guessing.
    let mut result = Vec::with_capacity(count as usize);
    for index in 0..count {
        let base = (offset + index * stride) as usize;
        let mut row = Dict::with_capacity(9);
        row.push_unique("record_index".into(), Value::from(index as i64));
        row.push_unique("byte_offset".into(), Value::from(base as i64));
        row.push_unique("expiration".into(), Value::from(u16_le(data, base + 8)));
        row.push_unique(
            "charges".into(),
            Value::List([10, 12, 14].iter().map(|at| Value::from(u16_le(data, base + at))).collect()),
        );
        row.push_unique("flags".into(), Value::from(u32_le(data, base + 16)));
        match reference(data, base, true, false) {
            Ok(name) => row.push_unique("resref".into(), Value::Str(name)),
            Err(error) => {
                if !allow_invalid {
                    return Err(error);
                }
                row.push_unique("resref".into(), Value::from(""));
                row.push_unique("raw_resref_hex".into(), Value::Str(hex(&data[base..base + 8])));
            }
        }
        result.push(row);
    }
    Ok(result)
}

/// Read a CRE V1.0; only discovery may keep dangling slot words.
pub fn parse_cre(data: &[u8], allow_dangling_slots: bool, allow_invalid_items: bool) -> Result<Dict> {
    header(data, b"CRE V1.0", 0x2D4)?;
    let mut sections = Sections::new(data, 0x2D4);
    for (offset, stride, label) in
        [(0x2A0, 12, "CRE known spells"), (0x2A8, 16, "CRE spell memorization info"), (0x2B0, 12, "CRE memorized spells")]
    {
        sections.add(u32_le(data, offset) as u64, u32_le(data, offset + 4) as u64, stride, label)?;
    }
    let slots_offset = u32_le(data, 0x2B8) as u64;
    let items_offset = u32_le(data, 0x2BC) as u64;
    let count = u32_le(data, 0x2C0) as u64;
    sections.add(slots_offset, 40, 2, "CRE item slots")?;
    sections.add(items_offset, count, 20, "CRE items")?;
    let effect_count = u32_le(data, 0x2C8) as u64;
    let effect_version = data[0x33];
    if effect_version > 1 {
        return Err(Error::scan("Unsupported CRE effect structure version."));
    }
    sections.add(u32_le(data, 0x2C4) as u64, effect_count, if effect_version == 0 { 48 } else { 264 }, "CRE effects")?;
    sections.verify()?;

    let mut rows = items(data, items_offset, count, 20, allow_invalid_items)?;
    let mut slot_lists: Vec<Vec<Value>> = vec![Vec::new(); rows.len()];
    let mut slots = Vec::with_capacity(38);
    let mut dangling = false;
    for slot in 0..38u64 {
        let item_index = u16_le(data, (slots_offset + slot * 2) as usize) as u64;
        if item_index == 0xFFFF {
            slots.push(Value::Null);
        } else if item_index >= count {
            if !allow_dangling_slots {
                return Err(Error::scan(format!(
                    "CRE item slot {slot} refers to an absent item record: index {item_index}, table count {count}."
                )));
            }
            slots.push(Value::from(item_index as i64));
            dangling = true;
        } else {
            slots.push(Value::from(item_index as i64));
            slot_lists[item_index as usize].push(Value::from(slot as i64));
        }
    }
    let mut unslotted = Vec::new();
    let mut aliased = Vec::new();
    let mut invalid_items = Vec::new();
    for (index, (row, slot_list)) in rows.iter_mut().zip(slot_lists).enumerate() {
        if slot_list.is_empty() {
            unslotted.push(Value::from(index as i64));
        }
        if slot_list.len() > 1 {
            aliased.push(Value::from(index as i64));
        }
        if row.contains_key("raw_resref_hex") {
            invalid_items.push(Value::from(index as i64));
        }
        row.push_unique("slots".into(), Value::List(slot_list));
    }
    let mut result = Dict::with_capacity(12);
    result.push_unique("format".into(), Value::from("CRE V1.0"));
    result.push_unique("name_strref".into(), Value::from(u32_le(data, 8)));
    result.push_unique("script_name".into(), Value::Str(text(data, 0x280, 32)));
    result.push_unique("items".into(), Value::List(rows.into_iter().map(Value::Dict).collect()));
    result.push_unique("slots".into(), Value::List(slots));
    result.push_unique("item_slots_offset".into(), Value::from(slots_offset as i64));
    result.push_unique("items_offset".into(), Value::from(items_offset as i64));
    result.push_unique("selected_weapon".into(), Value::from(u16_le(data, (slots_offset + 76) as usize)));
    result.push_unique("selected_weapon_ability".into(), Value::from(u16_le(data, (slots_offset + 78) as usize)));
    result.push_unique("unslotted_item_indexes".into(), Value::List(unslotted));
    result.push_unique("aliased_item_indexes".into(), Value::List(aliased));
    if dangling || !invalid_items.is_empty() {
        // Keep the header values and complete raw table. Do not invent a
        // source row or read an absent index as the empty-slot sentinel.
        let mut diagnostics = Dict::with_capacity(6);
        diagnostics.push_unique("cre_offset".into(), Value::Int(0));
        diagnostics.push_unique("item_count".into(), Value::from(count as i64));
        diagnostics.push_unique("items_offset".into(), Value::from(items_offset as i64));
        diagnostics.push_unique("item_slots_offset".into(), Value::from(slots_offset as i64));
        diagnostics.push_unique(
            "slot_words".into(),
            Value::List((0..40u64).map(|slot| Value::from(u16_le(data, (slots_offset + slot * 2) as usize))).collect()),
        );
        if !invalid_items.is_empty() {
            diagnostics.push_unique("invalid_item_indexes".into(), Value::List(invalid_items));
        }
        result.push_unique("inventory_diagnostics".into(), Value::Dict(diagnostics));
    }
    Ok(result)
}

fn are_sections(data: &[u8]) -> Result<Sections> {
    let mut sections = Sections::new(data, 0x11C);
    // Header offsets, count offsets, count widths, record sizes. Reading other
    // section spans prevents accepting an inventory that aliases an actor,
    // door, script variable or other structural bytes.
    let table: [(usize, usize, u8, u64, &str); 15] = [
        (0x54, 0x58, 2, 0x110, "actors"),
        (0x5C, 0x5A, 2, 0xC4, "regions"),
        (0x60, 0x64, 4, 0xC8, "spawn points"),
        (0x68, 0x6C, 4, 0x68, "entrances"),
        (0x70, 0x74, 2, 0xC0, "containers"),
        (0x78, 0x76, 2, 0x14, "items"),
        (0x7C, 0x80, 2, 4, "vertices"),
        (0x84, 0x82, 2, 0xD4, "ambients"),
        (0x88, 0x8C, 4, 0x54, "variables"),
        (0xA0, 0x9C, 4, 1, "explored bitmask"),
        (0xA8, 0xA4, 4, 0xC8, "doors"),
        (0xB0, 0xAC, 4, 0x4C, "animations"),
        (0xB8, 0xB4, 4, 0x68, "tiled objects"),
        (0xC4, 0xC8, 4, 0x34, "automap notes"),
        (0xCC, 0xD0, 4, 0x1C, "projectile traps"),
    ];
    for (offset, count_offset, width, stride, label) in table {
        let count = if width == 2 { u16_le(data, count_offset) } else { u32_le(data, count_offset) } as u64;
        sections.add(u32_le(data, offset) as u64, count, stride, &format!("ARE {label}"))?;
    }
    for (offset, size, label) in [(0xBC, 0x90, "song entries"), (0xC0, 0xE4, "rest interruptions")] {
        let value = u32_le(data, offset) as u64;
        sections.add(value, (value != 0) as u64, size, &format!("ARE {label}"))?;
    }
    sections.verify()?;
    // A projectile effect block is outside its fixed-size record table.
    for index in 0..u32_le(data, 0xD0) as u64 {
        let base = (u32_le(data, 0xCC) as u64 + index * 0x1C) as usize;
        sections.add(u32_le(data, base + 8) as u64, u16_le(data, base + 12) as u64, 1, "ARE projectile effects")?;
    }
    Ok(sections)
}

/// Read ARE V1.0 physical records, keeping unresolved ownership.
pub fn parse_are(
    data: &[u8],
    allow_dangling_slots: bool,
    allow_invalid_items: bool,
    allow_incomplete_actors: bool,
) -> Result<Dict> {
    header(data, b"AREAV1.0", 0x11C)?;
    let mut sections = are_sections(data)?;
    let count = u16_le(data, 0x76) as u64;
    let offset = u32_le(data, 0x78) as u64;
    let mut rows = items(data, offset, count, 20, false)?;
    let mut owners: Vec<Vec<i64>> = vec![Vec::new(); rows.len()];
    struct Container {
        fields: Dict,
        indexes: Vec<u64>,
    }
    let mut containers = Vec::new();
    for index in 0..u16_le(data, 0x74) as u64 {
        let base = (u32_le(data, 0x70) as u64 + index * 0xC0) as usize;
        let first = u32_le(data, base + 0x40) as u64;
        let number = u32_le(data, base + 0x44) as u64;
        if number != 0 && (first >= count || number > count - first) {
            return Err(Error::scan(format!("ARE container {index} refers outside its item table.")));
        }
        let indexes: Vec<u64> = (first..first + number).collect();
        for item_index in &indexes {
            owners[*item_index as usize].push(index as i64);
        }
        let name = text(data, base, 32);
        let mut fields = Dict::with_capacity(15);
        fields.push_unique("record_index".into(), Value::from(index as i64));
        fields.push_unique("byte_offset".into(), Value::from(base as i64));
        fields.push_unique("name".into(), Value::Str(name.clone()));
        fields.push_unique("x".into(), Value::from(u16_le(data, base + 0x20)));
        fields.push_unique("y".into(), Value::from(u16_le(data, base + 0x22)));
        fields.push_unique("type".into(), Value::from(u16_le(data, base + 0x24)));
        fields.push_unique("flags".into(), Value::from(u32_le(data, base + 0x28)));
        fields.push_unique("first_item_index".into(), Value::from(first as i64));
        fields.push_unique("item_count".into(), Value::from(number as i64));
        fields.push_unique(
            "item_indexes".into(),
            Value::List(indexes.iter().map(|value| Value::from(*value as i64)).collect()),
        );
        fields.push_unique("items".into(), Value::Null);
        fields.push_unique("owner".into(), Value::Str(text(data, base + 0x58, 32)));
        fields.push_unique("trap_script".into(), Value::Str(reference(data, base + 0x48, true, false)?));
        let dynamic = if crate::py::upper(&name).starts_with("T2") { Value::Str(name) } else { Value::Null };
        fields.push_unique("dynamic_item_table".into(), dynamic);
        containers.push(Container { fields, indexes });
    }
    for (row, owned) in rows.iter_mut().zip(&owners) {
        row.push_unique("container_indexes".into(), Value::List(owned.iter().map(|value| Value::Int(*value)).collect()));
        let single = if owned.len() == 1 { Value::Int(owned[0]) } else { Value::Null };
        row.push_unique("container_index".into(), single);
    }
    // Containers share their item records with the area's table.
    let containers: Vec<Value> = containers
        .into_iter()
        .map(|mut container| {
            let shared = container.indexes.iter().map(|index| Value::Dict(rows[*index as usize].clone())).collect();
            container.fields.insert("items", Value::List(shared));
            Value::Dict(container.fields)
        })
        .collect();

    let mut actors: Vec<Dict> = Vec::new();
    for index in 0..u16_le(data, 0x58) as u64 {
        let base = (u32_le(data, 0x54) as u64 + index * 0x110) as usize;
        let flags = u32_le(data, base + 0x28);
        let cre_offset = u32_le(data, base + 0x88) as u64;
        let cre_size = u32_le(data, base + 0x8C) as u64;
        if flags & 1 != 0 && (cre_offset != 0 || cre_size != 0) {
            return Err(Error::scan(format!("ARE actor {index} has conflicting external and embedded CRE storage.")));
        }
        let incomplete = (cre_offset != 0) != (cre_size != 0);
        if incomplete && !allow_incomplete_actors {
            return Err(Error::scan(format!("ARE actor {index} has incomplete embedded CRE storage.")));
        }
        // With no stored CRE, importers can use the external resref even if
        // the unloaded flag was not set. Any actual blob needs both values.
        let external = cre_offset == 0 && !incomplete;
        if !incomplete {
            sections.add(cre_offset, cre_size, 1, &format!("ARE actor {index} embedded CRE"))?;
        }
        let mut actor = Dict::with_capacity(12);
        actor.push_unique("record_index".into(), Value::from(index as i64));
        actor.push_unique("byte_offset".into(), Value::from(base as i64));
        actor.push_unique("name".into(), Value::Str(text(data, base, 32)));
        actor.push_unique("x".into(), Value::from(u16_le(data, base + 0x20)));
        actor.push_unique("y".into(), Value::from(u16_le(data, base + 0x22)));
        actor.push_unique("flags".into(), Value::from(flags));
        actor.push_unique("appearance_schedule".into(), Value::from(u32_le(data, base + 0x40)));
        actor.push_unique("cre_resref".into(), Value::Str(reference(data, base + 0x80, !external, !external)?));
        actor.push_unique("embedded_cre_offset".into(), Value::from(cre_offset as i64));
        actor.push_unique("embedded_cre_size".into(), Value::from(cre_size as i64));
        actor.push_unique("embedded_cre".into(), Value::Null);
        if incomplete {
            // Keep the raw offset/size instead of guessing a blob boundary or
            // silently treating this actor as a valid external template.
            actor.push_unique(
                "storage_diagnostics".into(),
                Value::Dict(crate::dict! {"reason" => "incomplete-embedded-cre"}),
            );
        }
        actors.push(actor);
    }
    sections.verify()?;
    for actor in actors.iter_mut() {
        let size = actor.get("embedded_cre_size").and_then(Value::as_int).unwrap_or(0) as usize;
        if size == 0 || actor.contains_key("storage_diagnostics") {
            continue;
        }
        let start = actor.get("embedded_cre_offset").and_then(Value::as_int).unwrap_or(0) as usize;
        let record_index = actor.get("record_index").and_then(Value::as_int).unwrap_or(0);
        let mut cre = parse_cre(slice(data, start, size), allow_dangling_slots, allow_invalid_items)
            .map_err(|error| Error::scan(format!("ARE actor {record_index} embedded CRE: {}", error.message)))?;
        let shift = |value: &mut Value| {
            if let Value::Int(number) = value {
                *number += start as i64;
            }
        };
        shift(cre.get_mut("item_slots_offset").expect("field"));
        if let Some(diagnostics) = cre.get_mut("inventory_diagnostics").and_then(Value::as_dict_mut) {
            diagnostics.insert("cre_offset", Value::from(start as i64));
        }
        // Empty item tables have no meaningful offset to rebase.
        let has_items = cre.get("items").and_then(Value::as_list).is_some_and(|list| !list.is_empty());
        if has_items {
            shift(cre.get_mut("items_offset").expect("field"));
        }
        if let Some(list) = cre.get_mut("items").and_then(Value::as_list_mut) {
            for item in list {
                shift(item.get_mut("byte_offset").expect("field"));
            }
        }
        actor.insert("embedded_cre", Value::Dict(cre));
    }
    let orphans: Vec<Value> =
        owners.iter().enumerate().filter(|(_, owned)| owned.is_empty()).map(|(index, _)| Value::from(index as i64)).collect();
    let aliased: Vec<Value> =
        owners.iter().enumerate().filter(|(_, owned)| owned.len() > 1).map(|(index, _)| Value::from(index as i64)).collect();
    let mut result = Dict::with_capacity(7);
    result.push_unique("format".into(), Value::from("ARE V1.0"));
    result.push_unique("script".into(), Value::Str(reference(data, 0x94, true, false)?));
    result.push_unique("items".into(), Value::List(rows.into_iter().map(Value::Dict).collect()));
    result.push_unique("containers".into(), Value::List(containers));
    result.push_unique("actors".into(), Value::List(actors.into_iter().map(Value::Dict).collect()));
    result.push_unique("orphan_item_indexes".into(), Value::List(orphans));
    result.push_unique("aliased_item_indexes".into(), Value::List(aliased));
    Ok(result)
}

/// Read store stock, keeping quantities and raw infinite-supply flags.
pub fn parse_sto(data: &[u8]) -> Result<Dict> {
    header(data, b"STORV1.0", 0x9C)?;
    let mut sections = Sections::new(data, 0x9C);
    for (offset, stride, label) in
        [(0x2C, 4, "purchased categories"), (0x34, 28, "sale items"), (0x4C, 20, "drinks"), (0x70, 12, "cures")]
    {
        sections.add(u32_le(data, offset) as u64, u32_le(data, offset + 4) as u64, stride, &format!("STO {label}"))?;
    }
    sections.verify()?;
    let mut rows = items(data, u32_le(data, 0x34) as u64, u32_le(data, 0x38) as u64, 28, false)?;
    for row in rows.iter_mut() {
        let base = row.get("byte_offset").and_then(Value::as_int).expect("offset") as usize;
        let flag = u32_le(data, base + 24);
        row.push_unique("quantity".into(), Value::from(u32_le(data, base + 20)));
        row.push_unique("infinite_supply_flag".into(), Value::from(flag));
        row.push_unique("infinite_supply".into(), Value::Bool(flag != 0));
    }
    let mut result = Dict::with_capacity(5);
    result.push_unique("format".into(), Value::from("STO V1.0"));
    result.push_unique("name_strref".into(), Value::from(u32_le(data, 12)));
    result.push_unique("type".into(), Value::from(u32_le(data, 8)));
    result.push_unique("flags".into(), Value::from(u32_le(data, 16)));
    result.push_unique("items".into(), Value::List(rows.into_iter().map(Value::Dict).collect()));
    Ok(result)
}
