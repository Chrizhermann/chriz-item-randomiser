//! Python-recorded parity cases for the `reward_copies` scenario (see
//! `configurator/parity/patchers/generate.py`), and the reference's
//! `tests/test_reward_copies.py` on the same synthetic dialogues.

mod common;

use cir_config::json::Value;
use cir_config::mode2_rewards::{patch_reward, route_reasons, Naming, Replies};
use cir_config::py::put_u32;
use cir_config::quest_options::Dialogue;
use cir_config::reward_copies::{RewardCopies, LIMIT};
use common::builders::{contains, outgoing, replace, sha, u32_at};
use common::mode2::{dialogue_fixture, empty_dialogue, specs};
use std::collections::{BTreeMap, BTreeSet, HashMap};

const SCENARIO: &str = "reward_copies";

#[test]
fn matches_the_reference() {
    common::run(SCENARIO);
}

type Files = BTreeMap<String, Vec<u8>>;

fn files(rows: &[(&str, &[u8])]) -> Files {
    rows.iter().map(|(name, data)| (name.to_string(), data.to_vec())).collect()
}

/// A dialogue name as a link names it: upper case, padded to eight bytes.
fn link_name(name: &str) -> Vec<u8> {
    let mut name = name.to_uppercase().into_bytes();
    name.resize(8, 0);
    name
}

/// A transition row, `struct.pack("<5I8sI", ...)`.
fn row(values: [u32; 5], name: &str, state: u32) -> Vec<u8> {
    let mut row: Vec<u8> = values.iter().flat_map(|value| value.to_le_bytes()).collect();
    row.extend(link_name(name));
    row.extend_from_slice(&state.to_le_bytes());
    row
}

fn state_rows(dialogue: &Dialogue, state: usize) -> (usize, usize) {
    (u32_at(&dialogue.states[state], 4) as usize, u32_at(&dialogue.states[state], 8) as usize)
}

/// `interject(data, name, state, npc, npc_data, copy_actions=...)`: WeiDU's
/// INTERJECT_COPY_TRANS (or _TRANS2 without `copy_actions`) on a state. The
/// state's replies are copied into a new state of the NPC's dialogue, which
/// the state now enters first. Returns both dialogues and the new state.
fn interject(data: &[u8], name: &str, state: usize, npc: &str, copy_actions: bool) -> (Vec<u8>, Vec<u8>, usize) {
    let mut route = Dialogue::new(data, name).unwrap();
    let (first, count) = state_rows(&route, state);
    let originals = route.transitions[first..first + count].to_vec();
    let mut other = empty_dialogue(npc);
    let (mut copies, mut moved) = (Vec::new(), Vec::new());
    for original in &originals {
        let flags = u32_at(original, 0);
        let mut copy = original.clone();
        if flags & 2 != 0 {
            other.triggers.push(route.triggers[u32_at(original, 12) as usize].clone());
            put_u32(&mut copy, 12, other.triggers.len() as u32 - 1);
        }
        if flags & 4 != 0 && copy_actions {
            other.actions.push(route.actions[u32_at(original, 16) as usize].clone());
            put_u32(&mut copy, 16, other.actions.len() as u32 - 1);
        } else if flags & 4 != 0 {
            // _TRANS2 keeps them with the jump.
            moved.push(route.actions[u32_at(original, 16) as usize].clone());
            put_u32(&mut copy, 0, flags & !4);
        }
        copies.push(copy);
    }
    let target = other.state(99999, copies);
    route.triggers.push(format!("InParty(\"{npc}\")\r\nGlobal(\"{npc}Talk\",\"GLOBAL\",0)\r\n").into_bytes());
    route.actions.push([format!("SetGlobal(\"{npc}Talk\",\"GLOBAL\",1)\r\n").into_bytes(), moved.into_iter().next().unwrap_or_default()].concat());
    let jump = row([2 | 4, 0xFFFF_FFFF, 0, route.triggers.len() as u32 - 1, route.actions.len() as u32 - 1], npc, target as u32);
    let rows: Vec<Vec<u8>> = std::iter::once(jump).chain(originals).collect();
    let at = route.transitions.len() as u32;
    put_u32(&mut route.states[state], 4, at);
    put_u32(&mut route.states[state], 8, rows.len() as u32);
    route.transitions.extend(rows);
    (route.render(), other.render(), target)
}

/// `link(data, name, state, other, other_state)`: a reply of `state` that
/// continues in another dialogue.
fn link(data: &[u8], name: &str, state: usize, other: &str, other_state: u32) -> Vec<u8> {
    let mut dialogue = Dialogue::new(data, name).unwrap();
    let (first, count) = state_rows(&dialogue, state);
    let mut rows = dialogue.transitions[first..first + count].to_vec();
    rows.push(row([1, 4242, 0, 0, 0], other, other_state));
    let at = dialogue.transitions.len() as u32;
    put_u32(&mut dialogue.states[state], 4, at);
    put_u32(&mut dialogue.states[state], 8, rows.len() as u32);
    dialogue.transitions.extend(rows);
    dialogue.render()
}

fn hashes(files: &Files) -> HashMap<String, Value> {
    files.iter().map(|(name, data)| (format!("dlg:{name}"), Value::from(sha(data)))).collect()
}

/// `analyse(files, scanned)`: the scan's hashes are those of `files` unless given.
fn analyse_with(files: &Files, scanned: Option<HashMap<String, Value>>, limit: usize) -> RewardCopies {
    let scanned = scanned.unwrap_or_else(|| hashes(files));
    RewardCopies::with_limit(&mut |name| Ok(files.get(name).cloned()), &scanned, limit, Naming::default()).unwrap()
}

fn analyse(files: &Files, scanned: Option<HashMap<String, Value>>) -> RewardCopies {
    analyse_with(files, scanned, LIMIT)
}

/// `actions_of(data, name, state)`: (trigger, actions) of each reply.
fn actions_of(data: &[u8], name: &str, state: usize) -> Vec<(Vec<u8>, Vec<u8>)> {
    let dialogue = Dialogue::new(data, name).unwrap();
    outgoing(&dialogue, state)
        .iter()
        .map(|row| {
            let trigger = dialogue.triggers[u32_at(row, 12) as usize].clone();
            let actions = if u32_at(row, 0) & 4 != 0 { dialogue.actions[u32_at(row, 16) as usize].clone() } else { Vec::new() };
            (trigger, actions)
        })
        .collect()
}

/// route -> copy dialogue -> state -> ordinals, from (route, dialogue, state, ordinals).
fn copies(rows: &[(&str, &str, usize, &[usize])]) -> BTreeMap<String, BTreeMap<String, Replies>> {
    let mut result: BTreeMap<String, BTreeMap<String, Replies>> = BTreeMap::new();
    for (route, resource, state, ordinals) in rows {
        let dialogues = result.entry(route.to_string()).or_default();
        dialogues.entry(resource.to_string()).or_default().insert(*state, ordinals.iter().copied().collect());
    }
    result
}

fn reasons(rows: &[(&str, &str)]) -> BTreeMap<String, String> {
    rows.iter().map(|(route, reason)| (route.to_string(), reason.to_string())).collect()
}

fn ogre() -> Vec<u8> {
    dialogue_fixture(&specs("dlg:uhogre01"))
}

/// The dialogue of `changed_reply`: the actions of `state`'s first reply changed.
fn changed_reply(data: &[u8], name: &str, state: usize, change: impl Fn(&[u8]) -> Vec<u8>) -> Vec<u8> {
    let mut dialogue = Dialogue::new(data, name).unwrap();
    let index = u32_at(&outgoing(&dialogue, state)[0], 16) as usize;
    dialogue.actions[index] = change(&dialogue.actions[index]);
    dialogue.render()
}

/// The five states of test_replies_the_route_cannot_gate_leave_it_out_with_their_reason.
fn chain() -> Vec<u8> {
    let mut chain = empty_dialogue("xnpcj");
    for number in 0..5 {
        let transition = chain.transition(Some(number + 1), Some(1), "", "", None).unwrap();
        chain.state(1, vec![transition]);
    }
    chain.render()
}

fn temple_files() -> Files {
    let temple = dialogue_fixture(&specs("dlg:bhoisig"));
    files(&[
        ("bhoisig", &link(&temple, "bhoisig", 0, "scyarryl", 19)),
        ("scyarryl", &dialogue_fixture(&specs("dlg:scyarryl"))),
        ("bhnalla", &dialogue_fixture(&specs("dlg:bhnalla"))),
        ("bharval", &dialogue_fixture(&specs("dlg:bharval"))),
    ])
}

#[test]
fn builders_match_the_reference_builders() {
    let fixture = common::load(SCENARIO);
    let recorded = |label: &str| -> Files {
        let args = fixture.args(&format!("copies {label}"));
        let rows = args.get("files").and_then(Value::as_dict).unwrap();
        rows.iter().map(|(name, value)| (name.to_string(), fixture.blob(value))).collect()
    };
    let (route, npc, state) = interject(&ogre(), "uhogre01", 22, "xnpcj", true);
    assert_eq!(recorded("interjection"), files(&[("uhogre01", &route), ("xnpcj", &npc)]));
    let (trans2, trans2_npc, _) = interject(&ogre(), "uhogre01", 22, "xnpcj", false);
    assert_eq!(recorded("trans2"), files(&[("uhogre01", &trans2), ("xnpcj", &trans2_npc)]));
    let (chained, first, _) = interject(&ogre(), "uhogre01", 22, "xnpca", true);
    let (first, second, other) = interject(&first, "xnpca", state, "xnpcb", true);
    let second = link(&second, "xnpcb", other, "xnpca", state as u32);
    assert_eq!(recorded("chained"), files(&[("uhogre01", &chained), ("xnpca", &first), ("xnpcb", &second)]));
    let (aran, sarah, _) = interject(&dialogue_fixture(&specs("dlg:aran")), "aran", 4, "k#sarahj", true);
    assert_eq!(recorded("two routes"), files(&[("aran", &aran), ("k#sarahj", &sarah)]));
    let wait = changed_reply(&npc, "xnpcj", state, |text| replace(text, b"Wait(1)", b"Wait(2)", None));
    assert_eq!(recorded("also gives"), files(&[("uhogre01", &route), ("xnpcj", &wait)]));
    let shown = changed_reply(&npc, "xnpcj", state, |_| b"TakePartyItem(\"shld26\")\r\nDisplayStringHead(Myself,1)\r\n".to_vec());
    assert_eq!(recorded("takes and shows"), files(&[("uhogre01", &route), ("xnpcj", &shown)]));
    let (skull, skull_npc, _) = interject(&ogre(), "uhogre01", 22, "hlskull", true);
    assert_eq!(recorded("cir changes hlskull"), files(&[("uhogre01", &skull), ("hlskull", &skull_npc)]));
    let genie = dialogue_fixture(std::slice::from_ref(&specs("dlg:trgeni01")[0]));
    let (genie_route, genie_npc, _) = interject(&genie, "trgeni01", 35, "xnpcj", true);
    assert_eq!(recorded("carried"), files(&[("trgeni01", &genie_route), ("xnpcj", &genie_npc)]));
    let (prince_route, prince_npc, prince_state) = interject(&dialogue_fixture(&specs("dlg:sahpr2")), "sahpr2", 5, "xnpcj", true);
    assert_eq!(recorded("prince"), files(&[("sahpr2", &prince_route), ("xnpcj", &prince_npc)]));
    let incremented = changed_reply(&prince_npc, "xnpcj", prince_state, |_| b"IncrementGlobal(\"villynatyjob\",\"GLOBAL\",1)\r\n".to_vec());
    assert_eq!(recorded("prince incremented"), files(&[("sahpr2", &prince_route), ("xnpcj", &incremented)]));
    let linked = link(&ogre(), "uhogre01", 3, "broken", 0);
    assert_eq!(recorded("broken"), files(&[("uhogre01", &linked), ("broken", &[b"DLG V1.0".as_slice(), &[0; 10]].concat())]));
    let chain_route = link(&ogre(), "uhogre01", 3, "xnpcj", 0);
    assert_eq!(recorded("limit 3"), files(&[("uhogre01", &chain_route), ("xnpcj", &chain())]));
    assert_eq!(recorded("temple"), temple_files());
}

#[test]
fn interjection_copies_are_found_and_gated_like_the_route_dialogue() {
    let (route, npc, state) = interject(&ogre(), "uhogre01", 22, "xnpcj", true);
    let found = analyse(&files(&[("uhogre01", &route), ("xnpcj", &npc)]), None);
    assert_eq!(found.copies, copies(&[("uhogre01", "dlg:xnpcj", state, &[0, 1])]));
    assert!(found.reasons.is_empty());
    assert_eq!(found.checked("uhogre01"), BTreeSet::from(["dlg:xnpcj".to_string()]));
    assert!(found.changed.is_empty());
    let patched = found.patches(&["uhogre01"]).unwrap()["dlg:xnpcj"].clone();
    assert!(found.patches(&["mgkhol01"]).unwrap().is_empty());
    // Each copy is gated exactly as the route dialogue gates the original.
    let gated = patch_reward("dlg:uhogre01", &route, None, None, Naming::default()).unwrap();
    let copied = actions_of(&patched, "xnpcj", state);
    assert_eq!(copied, actions_of(&gated, "uhogre01", 22)[1..]);
    assert_eq!(copied.iter().filter(|(_, text)| contains(text, b"GiveItemCreate(\"shld26\"")).count(), 2);
    assert!(copied.iter().all(|(trigger, _)| contains(trigger, b"Global(\"FLPR_QM2_14\",\"GLOBAL\",1)")));
    // The jump into the interjection gives nothing and stays as it was.
    assert_eq!(
        outgoing(&Dialogue::new(&gated, "uhogre01").unwrap(), 22)[0],
        outgoing(&Dialogue::new(&route, "uhogre01").unwrap(), 22)[0]
    );
}

#[test]
fn interject_copy_trans2_keeps_the_grant_in_the_route_dialogue() {
    let (route, npc, _) = interject(&ogre(), "uhogre01", 22, "xnpcj", false);
    let found = analyse(&files(&[("uhogre01", &route), ("xnpcj", &npc)]), None);
    assert!(found.copies.is_empty() && found.reasons.is_empty());
    assert!(route_reasons("dlg:uhogre01", &route, Naming::default()).unwrap().is_empty());
    let gated = Dialogue::new(&patch_reward("dlg:uhogre01", &route, None, None, Naming::default()).unwrap(), "uhogre01").unwrap();
    // The jump gives the reward now, so it is gated as well: three replies, two variants each.
    assert_eq!(outgoing(&gated, 22).len(), 6);
}

#[test]
fn chained_interjections_and_cycles() {
    let (route, first, state) = interject(&ogre(), "uhogre01", 22, "xnpca", true);
    let (first, second, other) = interject(&first, "xnpca", state, "xnpcb", true);
    // Back into the first one.
    let second = link(&second, "xnpcb", other, "xnpca", state as u32);
    let found = analyse(&files(&[("uhogre01", &route), ("xnpca", &first), ("xnpcb", &second)]), None);
    // The first one's state now starts with the jump into the second one.
    assert_eq!(found.copies, copies(&[("uhogre01", "dlg:xnpca", state, &[1, 2]), ("uhogre01", "dlg:xnpcb", other, &[0, 1])]));
    assert!(found.reasons.is_empty());
    let patched: BTreeSet<String> = found.patches(&["uhogre01"]).unwrap().into_keys().collect();
    assert_eq!(patched, BTreeSet::from(["dlg:xnpca".to_string(), "dlg:xnpcb".to_string()]));
}

#[test]
fn two_routes_in_one_reply_are_gated_together_in_the_copy() {
    let (route, npc, state) = interject(&dialogue_fixture(&specs("dlg:aran")), "aran", 4, "k#sarahj", true);
    let found = analyse(&files(&[("aran", &route), ("k#sarahj", &npc)]), None);
    let all: Vec<usize> = (0..8).collect();
    assert_eq!(found.copies, copies(&[("aran", "dlg:k#sarahj", state, &all), ("aran2", "dlg:k#sarahj", state, &all)]));
    let patched = found.patches(&["aran", "aran2"]).unwrap()["dlg:k#sarahj"].clone();
    let gated = patch_reward("dlg:aran", &route, None, None, Naming::default()).unwrap();
    let copied = actions_of(&patched, "k#sarahj", state);
    assert_eq!(copied, actions_of(&gated, "aran", 4)[1..]);
    assert_eq!(copied.len(), 32);
}

#[test]
fn replies_the_route_cannot_gate_leave_it_out_with_their_reason() {
    let original = ogre();
    let (route, npc, state) = interject(&original, "uhogre01", 22, "xnpcj", true);
    let changed = changed_reply(&npc, "xnpcj", state, |text| replace(text, b"Wait(1)", b"Wait(2)", None));
    let found = analyse(&files(&[("uhogre01", &route), ("xnpcj", &changed)]), None);
    assert_eq!(
        found.reasons,
        reasons(&[(
            "uhogre01",
            "XNPCJ.DLG state 0 also gives SHLD26, but not with a copy of a reward reply of UHOGRE01.DLG, \
             so the route cannot tell whether it is the same reward"
        )])
    );
    // The exact copy there is still gated, for revisions exported earlier.
    assert_eq!(found.copies, copies(&[("uhogre01", "dlg:xnpcj", state, &[1])]));
    // Taking, showing or testing the reward elsewhere is no conflict.
    let shown = changed_reply(&npc, "xnpcj", state, |_| b"TakePartyItem(\"shld26\")\r\nDisplayStringHead(Myself,1)\r\n".to_vec());
    assert!(analyse(&files(&[("uhogre01", &route), ("xnpcj", &shown)]), None).reasons.is_empty());
    // A copy where CIR changes the dialogue for something else.
    let (route, npc, _) = interject(&original, "uhogre01", 22, "hlskull", true);
    assert_eq!(
        analyse(&files(&[("uhogre01", &route), ("hlskull", &npc)]), None).reasons,
        reasons(&[(
            "uhogre01",
            "HLSKULL.DLG state 0 copies a reward reply of UHOGRE01.DLG, but CIR changes HLSKULL.DLG for something else"
        )])
    );
    // A carried reward: the copy's speaker does not carry it.
    let genie = dialogue_fixture(std::slice::from_ref(&specs("dlg:trgeni01")[0]));
    let (route, npc, _) = interject(&genie, "trgeni01", 35, "xnpcj", true);
    assert_eq!(
        analyse(&files(&[("trgeni01", &route), ("xnpcj", &npc)]), None).reasons,
        reasons(&[(
            "trgeni01",
            "XNPCJ.DLG state 0 copies a reward reply of TRGENI01.DLG, but there another speaker would hand \
             over the SW1H23 that TRGENI01.DLG's speaker carries"
        )])
    );
    // A dialogue the route leads into that cannot be read.
    let route = link(&original, "uhogre01", 3, "broken", 0);
    let broken = [b"DLG V1.0".as_slice(), &[0; 10]].concat();
    assert_eq!(
        analyse(&files(&[("uhogre01", &route), ("broken", &broken)]), None).reasons,
        reasons(&[("uhogre01", "BROKEN.DLG cannot be read as a dialogue, so the route cannot check it for copies of its reward")])
    );
    // Too many states to follow.
    let route = link(&original, "uhogre01", 3, "xnpcj", 0);
    assert_eq!(
        analyse_with(&files(&[("uhogre01", &route), ("xnpcj", &chain())]), None, 3).reasons,
        reasons(&[(
            "uhogre01",
            "UHOGRE01.DLG lead into more than 3 states of other dialogues; the route cannot check them all \
             for copies of its reward"
        )])
    );
}

#[test]
fn a_reply_elsewhere_that_ends_the_princes_job_leaves_his_place_out() {
    // His route hands over what he carries where his job ends. A copy of
    // that reply in an interjection, or another reply there that ends the
    // job, would end it without him handing over: the reward would stay
    // with him. One that names the job but does not end it changes nothing.
    let original = dialogue_fixture(&specs("dlg:sahpr2"));
    let (route, npc, state) = interject(&original, "sahpr2", 5, "xnpcj", true);
    let found = analyse(&files(&[("sahpr2", &route), ("xnpcj", &npc)]), None);
    assert!(found.copies.is_empty() && found.changed.is_empty());
    assert_eq!(found.checked("sahpr2"), BTreeSet::from(["dlg:xnpcj".to_string()]));
    let reason = format!(
        "XNPCJ.DLG state {state} also sets villynatyjob to 2, which ends the job of SAHPR2.DLG's speaker, but there \
         it would not hand over the reward it carries"
    );
    assert_eq!(found.reasons, reasons(&[("sahpr2", &reason)]));
    assert!(found.patches(&["sahpr2"]).unwrap().is_empty());
    // In the prince's own dialogue the route itself is unchanged: only his
    // reply hands over, the jump into the interjection gives nothing.
    assert!(route_reasons("dlg:sahpr2", &route, Naming::default()).unwrap().is_empty());
    let gated = Dialogue::new(&patch_reward("dlg:sahpr2", &route, None, None, Naming::default()).unwrap(), "sahpr2").unwrap();
    let handing: Vec<bool> =
        outgoing(&gated, 5).iter().map(|row| contains(&gated.actions[u32_at(row, 16) as usize], b"FLPR_Transfer(")).collect();
    assert_eq!(handing, [false, true]);
    for (actions, reason) in [
        (b"ActionOverride(\"sahpr2\",SetGlobal(\"VILLYNATYJOB\",\"GLOBAL\",2))\r\n".as_slice(), Some("also sets villynatyjob")),
        (b"SetGlobal(\"villynatyjob\",\"GLOBAL\",3)\r\n", None),
        (b"IncrementGlobal(\"villynatyjob\",\"LOCALS\",1)\r\n", None),
        (b"SetGlobal(\"villynatyjob\",\"GLOBAL\",2\r\n", Some("names villynatyjob in actions that cannot be read")),
        (
            b"IncrementGlobal(\"villynatyjob\",\"GLOBAL\",1)\r\n",
            Some("changes villynatyjob with IncrementGlobal(), which the route cannot follow"),
        ),
    ] {
        let changed = changed_reply(&npc, "xnpcj", state, |_| actions.to_vec());
        let found = analyse(&files(&[("sahpr2", &route), ("xnpcj", &changed)]), None);
        assert!(found.copies.is_empty());
        match reason {
            None => assert!(found.reasons.is_empty(), "{:?}", found.reasons),
            Some(reason) => assert!(found.reasons["sahpr2"].contains(reason), "{reason}: {:?}", found.reasons),
        }
    }
    // The other routes find their copies as before; the prince's job is not theirs.
    assert!(analyse(&files(&[("sahpr2", &original)]), None).reasons.is_empty());
}

#[test]
fn another_routes_own_grant_of_the_same_item_stays_that_routes() {
    // The temple messengers lead into the sculptor, who gives the mace for his own quest.
    let found = analyse(&temple_files(), None);
    assert!(!found.reasons.contains_key("bhoisig"));
    assert!(!found.copies.contains_key("bhoisig"));
    assert!(found.checked("bhoisig").contains("dlg:scyarryl"));
}

#[test]
fn our_copy_patch_is_recognised_and_a_changed_one_named() {
    let (route, npc, _) = interject(&ogre(), "uhogre01", 22, "xnpcj", true);
    let before = files(&[("uhogre01", &route), ("xnpcj", &npc)]);
    let found = analyse(&before, None);
    let applied = files(&[
        ("uhogre01", &patch_reward("dlg:uhogre01", &route, None, None, Naming::default()).unwrap()),
        ("xnpcj", &found.patches(&["uhogre01"]).unwrap()["dlg:xnpcj"]),
    ]);
    // The scan before Apply, and a rescan after it, both accept our patch.
    for scanned in [None, Some(hashes(&before))] {
        let again = analyse(&applied, scanned);
        assert_eq!(again.copies, found.copies);
        assert!(again.reasons.is_empty() && again.changed.is_empty());
        assert_eq!(again.patches(&["uhogre01"]).unwrap(), BTreeMap::from([("dlg:xnpcj".to_string(), applied["xnpcj"].clone())]));
    }
    let mut tampered = applied.clone();
    tampered.insert("xnpcj".into(), replace(&applied["xnpcj"], b"FLPR_", b"OTHER", Some(1)));
    assert_eq!(
        analyse(&tampered, None).reasons,
        reasons(&[("uhogre01", "XNPCJ.DLG carries a changed copy of the reward route's own patch")])
    );
    // Another change since the scan.
    let mut later = before.clone();
    later.insert("xnpcj".into(), [&npc[..], b"\0"].concat());
    assert_eq!(analyse(&later, Some(hashes(&before))).changed, BTreeSet::from(["dlg:xnpcj".to_string()]));
    let scanned = HashMap::from([
        ("dlg:uhogre01".to_string(), Value::from(sha(&route))),
        ("dlg:xnpcj".to_string(), Value::from(sha(&npc))),
    ]);
    let gone = analyse(&files(&[("uhogre01", &route)]), Some(scanned));
    assert_eq!(gone.changed, BTreeSet::from(["dlg:xnpcj".to_string()]));
}
