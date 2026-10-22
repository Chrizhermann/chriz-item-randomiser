//! Python-recorded parity cases for the `reward_route_rows` scenario
//! (see `configurator/parity/patchers/generate.py`): which extended rows are
//! rows of a reward route, and the reference's
//! `tests/test_original_catalog.py` on Boss Loot's rows of BG1's routes.

mod common;

use cir_config::json::{Dict, Value};
use cir_config::original_catalog::{mode2_route_source, mode2_route_target};
use cir_config::templates::build_template;

const SCENARIO: &str = "reward_route_rows";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

fn rows(kind: &str) -> Vec<Value> {
    let mut options = Dict::new();
    options.insert("scrolls", true);
    let extended = Value::from("extended");
    let draft = build_template("bg1-boss-loot", Some(&extended), Some(&extended), Some(&Value::Dict(options))).unwrap();
    draft.get(kind).and_then(Value::as_list).unwrap().clone()
}

fn row(rows: &[Value], identity: &str) -> Value {
    rows.iter().find(|row| row.get("id").and_then(Value::as_str) == Some(identity)).unwrap().clone()
}

fn changed(row: &Value, key: &str, value: &str) -> Value {
    let mut row = row.as_dict().unwrap().clone();
    row.insert(key, value);
    Value::Dict(row)
}

#[test]
fn boss_loots_rows_of_aldeths_varcis_and_nemphres_rewards_are_their_routes() {
    let (items, places) = (rows("items"), rows("targets"));
    for (preset, original) in [(62, 16), (71, 20), (339, 159)] {
        let sword = row(&items, &format!("bg1:boss-loot:mode2:item:{preset}"));
        assert_eq!(mode2_route_source(&sword).unwrap(), Some(format!("original-source:bg1:mode2:item:{original}")));
    }
    for (preset, original) in [(46, 16), (71, 20), (291, 136)] {
        let place = row(&places, &format!("bg1:boss-loot:mode2:target:{preset}"));
        assert_eq!(mode2_route_target(&place).unwrap(), Some(format!("bg1:mode2:target:{original}")));
    }
    // Only the preset's rows as its tables have them.
    let sword = row(&items, "bg1:boss-loot:mode2:item:62");
    for (key, value) in [("tier", "27"), ("source", "varci.dlg"), ("id", "bg1:boss-loot:mode2:item:63"), ("campaign", "bg2")] {
        assert_eq!(mode2_route_source(&changed(&sword, key, value)).unwrap(), None, "{key}");
    }
    let place = row(&places, "bg1:boss-loot:mode2:target:46");
    for (key, value) in [("area", "ar0617"), ("tier", "27"), ("id", "bg1:boss-loot:mode2:target:47")] {
        assert_eq!(mode2_route_target(&changed(&place, key, value)).unwrap(), None, "{key}");
    }
}
