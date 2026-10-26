"""The extended pools' quest reward routes, recognised by meaning in synthetic
resources, and the reviewed EET resources as regression evidence when present."""
import hashlib
from itertools import product
import json
from pathlib import Path
import re
import struct
import sys
import unittest

from lupa.lua51 import LuaRuntime as Lua51
from lupa.lua53 import LuaRuntime as Lua53
from lupa.luajit21 import LuaRuntime as LuaJIT

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from flir_config import mode2_rewards as rewards
from flir_config import translations
from flir_config.game_names import EetNames
from flir_config.game_text import GameText
from flir_config.profiles import ProfileError
from flir_config.quest_options import _Dialogue
from flir_config.quest_rewards import _blocks, _global_guard
from flir_config.resources import _Tlk
from flir_config.script_text import block_actions, block_parts, script_blocks
from test_postinstall_resources import make_tlk

# The reviewed EET resources and their exact patches (text routes with their
# own reward texts appended to an empty dialog.tlk, so a new route's texts
# leave the others' numbers alone). The adapter no longer pins these; they
# show that recognising by meaning patches them exactly as the reviewed
# patches did.
REVIEWED_EET = {
    "bcs:asysta01": ("f0f1773357f2280b7b6a05695ff278650110dc60898e2c7ddc1241867782fadd",
                     "26bf2a67793e7b05fe35781f85ec4885aac806520927c35deba9aed5a2f5b841"),
    "bcs:ddwell": ("4d2232892ce30f7e55333f696bb2717e5208e3d588e0ef77e990fc0077955544",
                   "d0422206eb8c983e6fc14dea29ec78a8607674dd74548c0ff29be93fbd0accdb"),
    # The book as CIR's installer prepares it for the ring (EET, BG2:EE alike).
    "bcs:ppbook": ("39b1f5119d8abc96d5158eec54d212475e639516cb35d86de27e7d6e4cbbd515",
                   "e501c882a3fccf42e7cd5643d36c54dd67b5c38f3518d252cf98c453ddf1062a"),
    "dlg:aldeth": ("c9c9ec1e62045e24c585040effad5ec9561a404df3b84c8eb971264b1ef242ef",
                   "b6a08cfc2f712b30335ab0eb3534662785b68d8ed77b414e75377c01780d0982"),
    "dlg:amcler01": ("5bac12a183463f6faa912b15e80c45c321cd2189f318c5056cf38b2150a6d79e",
                     "f1a7a795b6b22abf209fd9be5f3357e71ced3e432db3e86e5ee38c9ad88d65e9"),
    "dlg:ammayor": ("d37409df94285e4bdce88bdfdb38382790dca567dc6304744244d9d5504030d6",
                    "d71af3922a33954154f7f2745df7466647465ad3920b6a04548b6a9df73a2e05"),
    "dlg:aran": ("da4bebc9ceb58fad2d34cf261b73d458ece55ad70128c656ede9e44a8631cee1",
                 "26551e099ce0936a8e0da8e128ae41546650d058685091849cc6406a882d50a7"),
    "dlg:bharval": ("ef4e704dc616f3658410cb798bb48b323b49b3b419986ff0345eb81979fb2730",
                    "d1ca99803daf9f609ea13f438eb75fe65ffb87d0f417b483a4873ece6006f31a"),
    "dlg:bhnalla": ("038818ff7d8e4fc3a62e10530b74764657e26b3f44203ebd1877db7535d17ab3",
                    "69fa93487cb7f0f4aa9883733e5d8a784155dbb726e06d88c595f591dfd763d5"),
    "dlg:bhoisig": ("5a55df4f2a0b814027735467fbf69017d9a13b0cd749d8566cf4f57c1f932377",
                    "43ca1ecf37968c0737109cb06b8c9fbb522a2becbe51a89662ef1d14df3c52f7"),
    "dlg:gorbat1": ("c1da7c158296ed5e553942a735ebc84773007b099a4eadb1cc78a50142a0088a",
                    "39d7e944e7d9efb983bb34013e7be3a9ab7cca2825d18010f16a22da60443c27"),
    "dlg:gorcamb": ("76cdb11e99cdbd676d7af08f48713fceb01061ea6180e762e6be1c997f3b1de4",
                    "4e881c91d3f63ef88cdbdc8cdb7c0f862d129657bb9696fb13e9d24bec915df5"),
    "dlg:gorlum2": ("8228c02ac6dee8ac4652ad550a0fef83a8ddf2d59400fcb7c1e025f6395b5e9c",
                    "28a2a669d4388c155abfcece1857611260628717e38c3bb49b1af245ce1e4ca5"),
    "dlg:gortan1": ("27a8575386e30a36a1f00f63c2004a095f301b511cd310eb03e36606265f3f3c",
                    "0a70f5274d7eacfd4c0c1941266c7bd9be3e5332026f24116c3cadfa6c441ede"),
    "dlg:hendak": ("0f5a4956e7ee05cb5d6a02e2d61bcf8be0ab575ecd921af31656578330b4a804",
                   "b19099dbd5a42cbb653b1a7abba30aeda7dbdc5697ba0e95106fe39ba7b1b7c4"),
    "dlg:jugjer01": ("11b89e8c49ea1133b1402a3581e5c678b56fdf652dfeabb88abfd90faebca77b",
                     "f0c13318d605bacd0ca9758c078d875669dd12b82454e1b7a4327d868f631084"),
    "dlg:lehtin": ("a067828ca2055d4c3fd406c43672d9da3547e92b3da67a5eb84b341fdbebd2d1",
                   "980b634e457adec11ba48405c65dbc5f9c6d032eac0d51bfcf8703d1eb6c44bf"),
    "dlg:loutcapt": ("1bb3dc1337c087cc049f0d4fa431f4a7f909dbfacabf4dd30087d8b4f52f2d98",
                     "9a7db245aa8f59ff1743421ed4621b96391521f48e8f6eeafb0d44070a1ddd25"),
    "dlg:mgkhol01": ("d4762221de29275f29a3516980cf2b6360612a1670ad5733f3e599a1ba72e3ea",
                     "3a47fea6c2cdb1405267342bad65ecc58741481006153dd3a15b43c3807decfd"),
    "dlg:nemphr": ("088f49c9a4a41ed5e566f7cdad8263287bd519aa1ac1768888501ad779aa82e0",
                   "bdc6e2d1c2d23ec77b3b489cf1f9c7d58edd3269b61561fa1168cbc6c3cff72f"),
    "dlg:ppdjinn": ("06c1671065b549921263594db378d7610223cb16b70b7f718b3885b45b32af38",
                    "dae488faf269ff5d144fb33093c777c00f3a539f4778d85fc91bb1af60d750ca"),
    "dlg:ppstat01": ("7022962710da01f3d66088d8f7c5aa2a7963d7e994ff0d55f61dc5b64c9673ae",
                     "69505c964ee036a6d9b974407e34cbd4ff35f4cebd10373819c5ef2f5d50f73a"),
    "dlg:riftg03": ("4bb8a0866e3470ae558c108160ebef18c33de9f14ad3181ccf33ebd0d5e4912a",
                    "34e7c5e57d9e69394b40ce09f4c5cd5a5e6249d871b676c82bafbd3d707f3ff5"),
    "dlg:sahkng01": ("c76c8624052f7e179d42157e4081ddfcdbb101d2f00b819008bc99f5660785c1",
                     "9a7109aa36361ce3e57183e08b735993dbb946584d4b79d50dd5cc641050c70f"),
    # The prince hands over what he carries when his job ends (state 5).
    "dlg:sahpr2": ("b70d79c4264b4dade199d86146da2d44f9db4d445dfbb4eead5b350c3a1c7dcb",
                   "19575a07c412224fa86c7a117b352aea40470e0e8c4494b95f6b6f85a2556244"),
    # The treasury's destroys of the king's two reward items, gated.
    "bcs:sahtreas": ("a3e4e14c6e0e3ea5d3c193cb30808aabb4c320f6b9e4dbbe3fadf99deee40751",
                     "07f76c023620896b09a222f5ae2c7251ffd2b07c8ec18643ab65b7cadf8cbe9b"),
    "dlg:scsain": ("c2a43b0d67eca9b531aa591463f74d5333225a95f0be87f8d03ed1cf91f8dafe",
                   "efbc5faa5490e6f89b6ca54a30a02ba74871353e19f7c8f640439974c175caca"),
    "dlg:sctelwyn": ("d5dd048c43e16ba937ff583eef8f5966e549fb0c4e3f92679883d35fcdda1759",
                     "bcafc54734313b1513100537abb486fd42298c69795d647f525da7e06b5f9c39"),
    "dlg:scyarryl": ("f96880bdabcfe8393d9ad6df502d3d001e2d79913736e480f6cc717f012dd367",
                     "33b842e20c6602bdbd9e62da78ec7d79e402d8cb2006282ce7568257d776698c"),
    "dlg:trgeni01": ("4220763c55e065bfb2f500aa9d615a1a1e112ae3fc60eee061a3d9f3f59f7549",
                     "5cb6cc95b0461934c198b687b0e6d868576e21f80cbc3050cc72b05216369877"),
    "dlg:trhmer01": ("e5651fc5ef72780fe2ce9bb6ec8c3632665a5279d41cf79244fbef393304752e",
                     "21aac581490c963ecf778f2ffc760bb3c56cce5be72fd637f783e70eccdbdd75"),
    "dlg:uddemon": ("582fbcfecf3e71363dff1aa9bcef4dbc202d28c3af6e66e6a29840b342f1bdfd",
                    "b1976c5f2f0b948ec00f972348b5ef208b0ba127bbc5678297b133747836a042"),
    "dlg:udlesa": ("6c518483372ebe4073986dc2d41e4f7ae73a7bce133aab4289a29ef1be7c7e50",
                   "02e3d2ad97dad0ba3c42c82c11601c0d13373b733d8eafff1bab9799f484a8ab"),
    "dlg:udsilver": ("48f697db47942b74b4530975a5a4a46cac1a0cc850453b2b9d50ab173b01090c",
                     "593cdf5a0313db7065fb2c1eaafc8efc309485d3b5a654c24b585f397e94917b"),
    "dlg:udsvir03": ("5918919ea366eceb27f4096f025cde79c44310a9e57bcd49d123fdbb48352994",
                     "2003095671c146bb3d06b92fcc17df06dc18e5be4ab59a0e5d17bfd6db412e66"),
    "dlg:uhmay01": ("4213656595fdde6afeefee881597ddd62814ba886747bc4d60cfca25a2d852be",
                    "fddebc84098847bf55786d953b1f400eba8a97250343e8076a174f6e15f3919c"),
    "dlg:uhogre01": ("8706469468f7277542ba0e6d7fcf44b47a15d36c3879f505464e0a119be00b97",
                     "722213d1f53966780ab8bba2e2dbe400e64c14ced6da2ff9f48b43749b629e25"),
    "dlg:varci": ("78c3de46c5c07c5896e5ea568ef9a71aa6e53ff5b6cb7ec8d4274d9e5dfbb017",
                  "2ae4a7164f4200efec5e9676d7a1fe20c6b05978523015df31302c75c8495e09"),
}
# EET's own rename lists (EET/lib/pcu_dict.tph) name BG1's SHLD07 SHLD07A.
EET = {"game": "eet", "names": EetNames({"itm": {"shld07": "shld07a"}, "cre": {}, "dlg": {}, "bcs": {}, "sto": {}},
                                        True)}


def outgoing(dialogue, state):
    first, count = struct.unpack_from("<II", dialogue.states[state], 4)
    return dialogue.transitions[first:first + count]


def grant_in(actions, spec):
    """The grant of the route's item in one reply's actions, and its recipient."""
    verb = b"GiveItem" if spec.get("grant_kind") == "transfer" else b"GiveItemCreate"
    # A recipient may be an object function, as Varci's LastTalkedToBy(Myself).
    found = list(re.finditer(rb'(?i)\b' + verb + rb'\(\s*"' + re.escape(spec["item"].encode()) +
                             rb'"\s*,\s*([^,()]+?(?:\([^()]*\))?)\s*(?:,[^()]*)?\)', actions))
    assert len(found) == 1, actions
    return found[0][0], found[0][1]


def guarded(prior, guard):
    """A reply condition with a guard line added, as the adapter joins it."""
    return prior + (b"\r\n" if prior and not prior[-1:].isspace() else b"") + guard


def handover_line(spec):
    """The reward reply's condition by which its place's treasury goes to the payee."""
    campaign, _, number = spec["target_ids"][0].partition(":mode2:target:")
    return (f'EEex_LuaTrigger("return(FLPR_Handover==nil)or(FLPR_Handover(EEex_LuaTrigger_Object,'
            f'{number}{",1" if campaign == "bg1" else ""}))")\r\n').encode()


TRGENI_DEPARTURE = (b'EEex_LuaAction("EEex_Action_QueueResponseStringOnAIBase('
                   b'[[DestroySelf()]],EEex_LuaAction_Object)")')


def trgeni_terminal_actions():
    actions = ['SetGlobal("zahraajob","GLOBAL",2)', 'SetGlobal("geniesgone","GLOBAL",1)',
               'AddexperienceParty(10000)']
    effects = ['CreateVisualEffectObject("SPDISPMA",Myself)', 'Wait(1)',
               'CreateVisualEffectObject("SPFLESHS",Myself)', 'DestroySelf()']
    for other in ("trgeni02", "trgeni03"):
        actions.extend(f'ActionOverride("{other}",{action})' for action in effects)
    return "\r\r\n".join(actions + effects) + "\r\r\n"


def fixture_target(quest, state):
    """Where a fixture reply leads: TRGENI's grants lead on to the genies' departure."""
    if quest == "trgeni01" and state in (35, 36, 37, 38, 40):
        return {35: 36, 40: 36, 36: 37, 37: 38}.get(state)
    return 2


def dialogue_fixture(specs):
    specs = (specs,) if isinstance(specs, dict) else specs
    spec = specs[0]
    header = bytearray(b"DLG V1.0" + bytes(44))
    struct.pack_into("<10I", header, 8, 0, 52, 0, 52, 52, 0, 52, 0, 52, 0)
    dialogue = _Dialogue(bytes(header), spec["quest"])
    required = set(row for spec in specs for row in spec["branches"])
    for state in range(max(row[0] for row in required) + 2):
        count = max((row[1] for row in required if row[0] == state), default=0) + 1
        transitions = []
        for ordinal in range(count):
            action = 'SetGlobal("Untouched","GLOBAL",8)\r\r\n'
            if (state, ordinal) in required:
                action += 'GiveItemCreate("misc42",Player2,0,0,0)\r\r\n'
                for grant_spec in specs:
                    if (state, ordinal) in grant_spec["branches"] and grant_spec.get("job_end"):
                        # A reply that ends the speaker's job and gives nothing.
                        job, value = grant_spec["job_end"]
                        action += f'SetGlobal("{job}","GLOBAL",{value})\r\r\n'
                    elif (state, ordinal) in grant_spec["branches"]:
                        charges = ",".join(map(str, grant_spec["payload"]["charges"]))
                        if grant_spec["quest"] == "amcler01":
                            action += 'DisplayStringHead(Myself,74025)\r\r\n'
                        if grant_spec.get("grant_kind") == "transfer":
                            action += f'GiveItem("{grant_spec["item"]}",{grant_spec["recipient"]})\r\r\n'
                        else:
                            action += f'GiveItemCreate("{grant_spec["item"]}",{grant_spec["recipient"]},{charges})\r\r\n'
            action += 'EraseJournalEntry(12345)\r\r\nWait(1)\r\r\nEscapeArea()'
            if spec["quest"] == "trgeni01" and state in (37, 38):
                action = trgeni_terminal_actions()
            transitions.append(dialogue.transition(fixture_target(spec["quest"], state), reply=9876,
                               journal=12345, trigger=f'Global("OriginalCondition","GLOBAL",{ordinal})',
                               action=action))
        dialogue.state(555 + state, transitions)
    return dialogue.render()


def layout_dialogue(name, states, *, beamdog=True):
    """A DLG as Beamdog's compiler writes it (LF between lines, none after the
    last) or as WeiDU does (CRLF after every line).

    <states> lists each state's replies as (trigger, actions, next state or None).
    """
    header = bytearray(b"DLG V1.0" + bytes(44))
    struct.pack_into("<10I", header, 8, 0, 52, 0, 52, 52, 0, 52, 0, 52, 0)
    dialogue = _Dialogue(bytes(header), name)
    line = (lambda text: text) if beamdog else (lambda text: text.replace("\n", "\r\n"))
    for number, replies in enumerate(states):
        dialogue.state(60000 + number, [dialogue.transition(target, reply=70000 + number, trigger=line(trigger),
                                                            action=line(actions))
                                        for trigger, actions, target in replies])
    if beamdog:
        dialogue.triggers = [text.rstrip(b"\r\n") for text in dialogue.triggers]
        dialogue.actions = [text.rstrip(b"\r\n") for text in dialogue.actions]
    return dialogue.render()


def with_actions(data, name, changes):
    """The dialogue with the actions of some replies replaced: {(state, ordinal): text}."""
    dialogue = _Dialogue(data, name)
    for (state, ordinal), text in changes.items():
        row = outgoing(dialogue, state)[ordinal]
        dialogue.actions[struct.unpack_from("<I", row, 16)[0]] = text
    return dialogue.render()


def obj(selector=0):
    return f'0 0 0 0 0 0 0 {selector} 0 0 0 0 ""OB\n'


def action(opcode, value=0, text="", target=0, actor=0, second=""):
    return (f'AC\n{opcode}OB\n{obj(actor)}OB\n{obj(target)}OB\n{obj()}'
            f'{value} 0 0 0 0"{text}" "{second}" AC\n')


def block(actions, condition="AR1202Quest"):
    return alternatives(actions, condition=condition, weight=100)


def alternatives(*responses, condition="AR1202Quest", weight=50):
    """A block whose responses the engine chooses between by weight."""
    condition = f'TR\n16399 1 0 0 0 "{condition}" "" OB\n{obj()}TR\n'
    return ('CR\nCO\n' + condition + 'CO\nRS\n' +
            "".join(f'RE\n{weight}' + actions + 'RE\n' for actions in responses) + 'RS\nCR\n')


def well_reward(target=21, display=40850):
    return (action(30, 2, "AR1202DragonWell") + action(151, display, target=1) +
            action(140, text="helm21", target=target) + action(177))


def script_fixture(name):
    if name == "asysta01":
        return ('SC\n' + block(action(164, 20000) + action(82, text="Helm19") +
                                action(82, text="Misc8y") + action(30, 2, "AR1512PlantedItems")) + 'SC\n').encode()
    other = block(action(151, 40849, target=1))
    return ('SC\n' + other + block(well_reward()) + other + 'SC\n').encode()


def trigger(opcode, value=0, text="", negate=0):
    return f'TR\n{opcode} {value} {negate} 0 0 "{text}" "" OB\n{obj()}TR\n'


def book_block(triggers, actions):
    return 'CR\nCO\n' + "".join(triggers) + 'CO\nRS\nRE\n100' + actions + 'RE\nRS\nCR\n'


BOOK_GIFTS = action(140, text="scrl8z", target=17) + action(140, text="scrl9b", target=17)


def book_fixture(installed=True):
    """PPBOOK.BCS as the game has it, or as CIR's installer splits its last page
    for the ring (FLPR_QBOOK): that page gives the ring and both scrolls to the
    reader (LastTrigger)."""
    clicked, page = trigger(112), trigger(16399, 5, "AR1513page")
    turn = action(151, 50658, target=1) + action(109, 1, "AR1513page")
    ring = action(140, text="ring09", target=17)
    earlier = book_block([clicked, trigger(16399, 4, "AR1513page")],
                         action(151, 50657, target=1) + action(109, 1, "AR1513page"))
    if installed:
        event = action(30, 1, "GLOBALFLPR_EBOOK")
        pages = (book_block([clicked, page, trigger(16399, 1, "GLOBALFLPR_QBOOK")],
                            turn + action(30, 1, "GLOBALFLPR_XBOOK") + BOOK_GIFTS + event) +
                 book_block([clicked, page, trigger(16399, 1, "GLOBALFLPR_QBOOK", negate=1)],
                            turn + ring + BOOK_GIFTS + event))
    else:
        pages = book_block([clicked, page], turn + ring + BOOK_GIFTS)
    return ('SC\n' + earlier + pages + 'SC\n').encode()


# Once the king's job is done (sahkngjob 2, just before he pays), SAHTREAS.BCS
# destroys the items his reward reply gives, the treasury's rod among them.
KING_PAID = trigger(16399, 2, "GLOBALsahkngjob")
KING_DESTROYS = action(169, text="RODS02") + action(169, text="BRAC19")
NO_ACTION = action(0)


def treasury_fixture(*blocks):
    """SAHTREAS.BCS as the game has it, or with these blocks."""
    return ('SC\n' + "".join(blocks or (book_block([KING_PAID], KING_DESTROYS),)) + 'SC\n').encode()


def route_guard(flag, selected):
    """The guard of a route's selected flag, as compiled."""
    return trigger(16399, 1, "GLOBAL" + flag, negate=0 if selected else 1)


class Mode2RewardTests(unittest.TestCase):
    def synthetic_patch(self, name, original):
        result = rewards.patch_reward(name, original, ddwell_ref=100001)
        self.assertEqual(rewards.patch_reward(name, result), result)
        self.assertFalse(rewards.own_patch(name, original))
        self.assertTrue(rewards.own_patch(name, result))
        self.assertEqual(rewards.route_reasons(name, original), {})
        self.assertEqual(rewards.route_reasons(name, result), {})
        tampered = result.replace(b"FLPR_", b"OTHER", 1)
        with self.assertRaisesRegex(ProfileError, "carries a changed copy of the reward route's own patch"):
            rewards.patch_reward(name, tampered)
        self.assertIn("changed copy", next(iter(rewards.route_reasons(name, tampered).values())))
        return result

    def check_dialogue(self, name, original, result, action_refs=None, branches=None, naming=None):
        spec = rewards.resource_specs("dlg:" + name, **(naming or {}))[0]
        branches = spec["branches"] if branches is None else branches
        before, after = _Dialogue(original, name), _Dialogue(result, name)
        self.assertEqual(after.state_triggers, before.state_triggers)
        self.assertEqual(after.actions[:len(before.actions)], before.actions)
        for index, state in enumerate(before.states):
            self.assertEqual(after.states[index][:4], state[:4])
            self.assertEqual(after.states[index][12:], state[12:])
            old, new = outgoing(before, index), outgoing(after, index)
            cursor = 0
            for ordinal, transition in enumerate(old):
                if (index, ordinal) not in branches:
                    if name == "trgeni01" and index in (37, 38):
                        self.assertEqual(new[cursor][:16], transition[:16])
                        self.assertEqual(new[cursor][20:], transition[20:])
                        original_action = before.actions[struct.unpack_from("<I", transition, 16)[0]]
                        changed = after.actions[struct.unpack_from("<I", new[cursor], 16)[0]]
                        self.assertEqual(changed.count(TRGENI_DEPARTURE), 1)
                        self.assertEqual(changed.replace(TRGENI_DEPARTURE, b"DestroySelf()"), original_action)
                        self.assertNotIn(b"DestroySelf()", changed.splitlines())
                        for other in (b"trgeni02", b"trgeni03"):
                            self.assertIn(b'ActionOverride("' + other + b'",DestroySelf())', changed)
                    else:
                        self.assertEqual(new[cursor], transition)
                    cursor += 1
                    continue
                flags, _, _, trigger, action_id = struct.unpack_from("<5I", transition)
                original_action = before.actions[action_id]
                prior = before.triggers[trigger] if flags & 2 else b""
                grant, recipient = grant_in(original_action, spec)
                for selected in (False, True):
                    branch = new[cursor]; cursor += 1
                    self.assertEqual(struct.unpack_from("<I", branch)[0], flags | 2)
                    self.assertEqual(branch[4:12], transition[4:12])  # Reply and journal.
                    self.assertEqual(branch[20:], transition[20:])  # Link/exit state.
                    trigger_id, action_id = struct.unpack_from("<II", branch, 12)
                    self.assertEqual(after.triggers[trigger_id], guarded(prior,
                        f'{"" if selected else "!"}Global("{spec["selected_global"]}","GLOBAL",1)\r\n'.encode()))
                    changed = after.actions[action_id]
                    if spec.get("inventory_target"):
                        # The target's row, and a BG1 row's campaign (1).
                        target = spec["target_ids"][0].rsplit(":", 1)[1]
                        campaign = ",1" if spec["target_ids"][0].startswith("bg1:") else ""
                        event = (f'\r\nEEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,'
                                 f'{target}{campaign}))")').encode()
                        self.assertEqual(changed.count(event), 1)
                        self.assertNotIn(spec["recipient_local"].encode(), changed)
                        self.assertNotIn(spec["event_global"].encode(), changed)
                        restored = changed.replace(event, b"", 1)
                        if selected and action_refs:
                            for old_ref, new_ref in action_refs.items():
                                self.assertEqual(restored.count(f'DisplayStringHead(Myself,{new_ref})'.encode()), 1)
                                restored = restored.replace(f'DisplayStringHead(Myself,{new_ref})'.encode(),
                                                            f'DisplayStringHead(Myself,{old_ref})'.encode(), 1)
                    else:
                        # The grant's own recipient, as this reply names it.
                        marker = (b"ActionOverride(" + recipient +
                                  f',SetGlobal("{spec["recipient_local"]}","LOCALS",1))\r\n'.encode())
                        event = f'\r\nSetGlobal("{spec["event_global"]}","GLOBAL",1)'.encode()
                        self.assertEqual(changed.count(marker), 1)
                        self.assertEqual(changed.count(event), 1)
                        self.assertLess(changed.index(marker), changed.index(event))
                        restored = changed.replace(marker, b"", 1).replace(event, b"", 1)
                    if selected:
                        self.assertNotIn(grant, changed)
                        consumed = f'SetGlobal("{spec["consumed_global"]}","GLOBAL",1)'.encode()
                        restored = restored.replace(consumed, grant, 1)
                    self.assertEqual(restored, original_action)
                    if b"EscapeArea()" in changed:
                        self.assertLess(changed.index(event), changed.index(b"EscapeArea()"))
                    if b"DestroySelf()" in changed:
                        self.assertLess(changed.index(event), changed.index(b"DestroySelf()"))
            self.assertEqual(cursor, len(new))

    def test_dialogue_both_campaign_routes_keep_exact_recipient_and_surrounding_actions(self):
        # Two routes of one dialogue have their own tests (check_pair), so has
        # the route without a grant (check_job_end).
        for resource in rewards.RESOURCE_REWARDS:
            if (resource.startswith("dlg:") and len(rewards.RESOURCE_REWARDS[resource]) == 1 and
                    resource not in rewards.TEXT_REFS and
                    not rewards.REWARDS[rewards.RESOURCE_REWARDS[resource][0]].get("job_end")):
                name = resource[4:]
                with self.subTest(resource=name):
                    original = dialogue_fixture(rewards.resource_specs(resource))
                    self.check_dialogue(name, original, self.synthetic_patch(name, original))

    def test_grants_are_found_by_meaning_in_another_layout_with_another_mods_replies(self):
        # Beamdog-compiled texts without trailing line breaks, other strrefs and
        # state numbers, letter case and spacing, another recipient and charges.
        # Another mod added a reply that gives nothing and a path that gives the
        # reward too: every reply that gives it is gated, nothing else changes.
        states = [[("", 'SetGlobal("OgreTalk","LOCALS",1)', 9)] for _ in range(9)]
        states.append([
            ('PartyHasItem("misc5x")',
             'TakePartyItem("misc5x")\nGiveItemCreate("SHLD26",LastTalkedToBy,0,0,0)\nAddexperienceParty(9000)', None),
            ('!PartyHasItem("misc5x")', 'GIVEITEMCREATE("shld26",Player1,1,0,0)\nEscapeArea()', None),
            ('Global("OtherMod","GLOBAL",1)', 'SetGlobal("OtherMod","GLOBAL",2)', 12)])
        states += [[("", "", None)], [("", "", None)],
                   [("", ' GiveItemCreate( "Shld26" , LastTalkedToBy , 0 , 0 , 0 ) \nSetGlobal("OtherMod","GLOBAL",3)',
                     None)]]
        gated = ((9, 0), (9, 1), (12, 0))
        for beamdog in (True, False):
            with self.subTest(beamdog=beamdog):
                original = layout_dialogue("uhogre01", states, beamdog=beamdog)
                result = self.synthetic_patch("uhogre01", original)
                self.check_dialogue("uhogre01", original, result, branches=gated)
                after = _Dialogue(result, "uhogre01")
                # Both gated replies became two each; the added reply follows.
                actions = after.actions[struct.unpack_from("<I", outgoing(after, 9)[4], 16)[0]]
                self.assertEqual(actions.rstrip(b"\r\n"), b'SetGlobal("OtherMod","GLOBAL",2)')
                self.assertIn(b'ActionOverride(Player1,SetGlobal("FLPR_RM2_14","LOCALS",1))',
                              b"".join(after.actions))

    def test_routes_the_check_cannot_recognise_name_their_reason(self):
        original = dialogue_fixture(rewards.resource_specs("dlg:uhogre01"))
        grant = 'GiveItemCreate("shld26",LastTalkedToBy,0,0,0)'
        cases = (
            ({(22, 0): b"GiveGoldForce(500)", (22, 1): b"GiveGoldForce(500)"},
             "UHOGRE01.DLG has no reply that gives SHLD26"),
            ({(22, 0): f"{grant}\r\n{grant}".encode()}, "UHOGRE01.DLG state 22 gives SHLD26 twice in one reply"),
            ({(22, 1): f'{grant}\r\nGiveItem("shld26",Player2)'.encode()},
             r"UHOGRE01.DLG state 22 also gives SHLD26 with GiveItem\(\), which the route cannot gate"),
            ({(23, 0): b'TakeItemReplace("Shld26","misc5x",LastTalkedToBy)'},
             r"UHOGRE01.DLG state 23 also gives SHLD26 with TakeItemReplace\(\), which the route cannot gate"),
            ({(22, 0): b'ActionOverride(Player2,GiveItemCreate("shld26",Myself,0,0,0))'},
             r"UHOGRE01.DLG state 22 gives SHLD26 through ActionOverride\(\), whose recipient the route "
             "cannot mark"),
            ({(22, 0): b'GiveItemCreate("shld26",LastTalkedToBy,0,0,0'},
             "UHOGRE01.DLG state 22 names SHLD26 in actions that cannot be read"),
            ({(22, 0): b'GiveItemCreate("shld26")'}, "UHOGRE01.DLG state 22 gives SHLD26 without a recipient"))
        for changes, reason in cases:
            with self.subTest(reason=reason):
                data = with_actions(original, "uhogre01", changes)
                self.assertRegex(rewards.route_reasons("dlg:uhogre01", data)["uhogre01"], "^" + reason + "$")
                self.assertEqual(set(rewards.route_reasons("dlg:uhogre01", data)), {"uhogre01"})
                self.assertFalse(rewards.own_patch("dlg:uhogre01", data))
                with self.assertRaisesRegex(ProfileError, reason):
                    rewards.patch_reward("dlg:uhogre01", data)
        broken = b"DLG V1.0" + bytes(10)
        self.assertEqual(rewards.route_reasons("dlg:uhogre01", broken),
                         {"uhogre01": "UHOGRE01.DLG cannot be read as a dialogue"})
        # A text route needs the text that names the reward where it gives it.
        plain = dialogue_fixture(rewards.resource_specs("dlg:ppdjinn"))
        self.assertEqual(rewards.route_reasons("dlg:ppdjinn", plain),
                         {"ppdjinn": "PPDJINN.DLG no longer shows the text that names the reward (48995)"})
        # Compiled scripts.
        other = block(action(151, 40849, target=1))
        well = lambda *blocks: ('SC\n' + "".join(blocks) + 'SC\n').encode()
        script_cases = (
            (well(other, other), "DDWELL.BCS has no block that gives HELM21"),
            (well(other, block(well_reward() + action(140, text="HELM21", target=21))),
             "DDWELL.BCS block 1 gives HELM21 twice in one response"),
            (well(other, block(well_reward().replace(action(140, text="helm21", target=21),
                                                     action(140, text="helm21", target=21, actor=17)))),
             r"DDWELL.BCS block 1 gives HELM21 through ActionOverride\(\), whose recipient the route "
             "cannot mark"),
            (well(other, block(well_reward() + action(82, text="helm21"))),
             r"DDWELL.BCS block 1 also gives HELM21 with CreateItem\(\), which the route cannot gate"),
            (well(other, alternatives(well_reward(), action(363, text="shop01", second="helm21"))),
             r"DDWELL.BCS block 1 also gives HELM21 with AddStoreItem\(\), which the route cannot gate"),
            (well(other, block(well_reward(display=40849))),
             r"DDWELL.BCS no longer shows the text that names the reward \(40850\)"),
            (b"SC\nCR\nCO\nSC\n", "DDWELL.BCS cannot be read as a compiled script: its last block is not closed"),
            (script_fixture("ddwell").replace(b"\n", b"\r\n", 3),
             "DDWELL.BCS cannot be read as a compiled script: its line endings are mixed"))
        for data, reason in script_cases:
            with self.subTest(reason=reason):
                self.assertRegex(rewards.route_reasons("bcs:ddwell", data)["ddwell"], "^" + reason + "$")
                with self.assertRaisesRegex(ProfileError, reason):
                    rewards.patch_reward("bcs:ddwell", data, ddwell_ref=100001)
        self.assertEqual(rewards.route_reasons("bcs:asysta01", script_fixture("asysta01").replace(
                         action(82, text="Helm19").encode(), action(140, text="Helm19").encode())),
                         {"asysta01": "ASYSTA01.BCS block 0 also gives HELM19 with GiveItemCreate(), "
                                      "which the route cannot gate"})

    def test_actions_that_only_take_show_or_test_the_reward_never_stop_a_route(self):
        # After a moved reward these find it gone, as when the player sold it.
        original = dialogue_fixture(rewards.resource_specs("dlg:uhogre01"))
        grant = 'GiveItemCreate("shld26",LastTalkedToBy,0,0,0)'
        data = with_actions(original, "uhogre01", {
            (22, 1): f'TakePartyItem("misc5x")\r\n{grant}\r\nDestroyItem("SHLD26")'.encode(),
            (23, 0): b'TakeItemReplace("misc5x","shld26",LastTalkedToBy)\r\nActionOverride(Player2,'
                     b'DestroyItem("shld26"))\r\nSetGlobal("SHLD26","GLOBAL",1)'})
        self.assertEqual(rewards.route_reasons("dlg:uhogre01", data), {})
        result = self.synthetic_patch("uhogre01", data)
        self.check_dialogue("uhogre01", data, result, branches=((22, 0), (22, 1)))
        # A script block chooses one of its responses: each may give the
        # reward once, and every display of the reward's text changes.
        other = block(action(151, 40849, target=1))
        twice_shown = well_reward() + action(151, 40850, target=2) + action(116, text="HELM21")
        script = ('SC\n' + other + alternatives(well_reward(), twice_shown) + other + 'SC\n').encode()
        self.assertEqual(rewards.route_reasons("bcs:ddwell", script), {})
        patched = self.synthetic_patch("ddwell", script)
        blocks = script_blocks(patched).blocks
        self.assertEqual(len(blocks), 4)
        unselected, selected = blocks[1], blocks[2]
        consumed = action(30, 1, "GLOBALFLPR_XM2_27").encode()
        event = action(30, 1, "GLOBALFLPR_EM2_27").encode()
        self.assertEqual(unselected.count(action(140, text="helm21", target=21).encode()), 2)
        self.assertEqual(unselected.count(event), 2)
        self.assertEqual(selected.count(consumed), 2)
        self.assertNotIn(b'"helm21"', selected)
        self.assertEqual(selected.count(b"151OB"), 3)
        self.assertEqual(selected.count(b"100001 0 0 0 0"), 3)
        self.assertIn(action(116, text="HELM21").encode(), selected)

    def test_compiled_grants_keep_owner_or_original_object_and_do_not_move_other_rewards(self):
        for name in ("asysta01", "ddwell"):
            with self.subTest(resource=name):
                original = script_fixture(name)
                result = self.synthetic_patch(name, original)
                spec = rewards.REWARDS[name]
                old, _, _ = _blocks(original, 1 if name == "asysta01" else 3, b"\n")
                new, _, _ = _blocks(result, len(old) + 1, b"\n")
                index = spec["branches"][0]
                self.assertEqual(new[:index], old[:index])
                self.assertEqual(new[index + 2:], old[index + 1:])
                for selected, branch in enumerate(new[index:index + 2]):
                    grant = action(82, text="Helm19") if name == "asysta01" else action(140, text="helm21", target=21)
                    self.assertEqual(branch.count(grant.encode()), 0 if selected else 1)
                    self.assertEqual(branch.count(spec["event_global"].encode()), 1)
                    self.assertEqual(branch.count(spec["consumed_global"].encode()), selected)
                    if name == "asysta01":
                        self.assertNotIn(b"LOCALSFLPR_R", branch)
                        self.assertIn(action(82, text="Misc8y").encode(), branch)
                        self.assertIn(action(30, 2, "AR1512PlantedItems").encode(), branch)
                    else:
                        marker = (f'AC\n30OB\n{obj(21)}OB\n{obj()}OB\n{obj()}'
                                  f'1 0 0 0 0"LOCALS{spec["recipient_local"]}" "" AC\n').encode()
                        self.assertIn(marker, branch)
                        self.assertLess(branch.index(marker), branch.index(b'GLOBAL' + spec["event_global"].encode()))
                        self.assertIn(action(151, 100001 if selected else 40850, target=1).encode(), branch)
                        self.assertIn(action(177).encode(), branch)

    def test_script_blocks_are_found_by_meaning_in_another_layout(self):
        # CRLF line ends, another mod's block before the reward, the reward
        # given to another object, and another mod's second block that gives
        # it without the text: both blocks are gated, the text changes where
        # it is shown, every other block stays as it is.
        added = block(action(30, 1, "OtherModWell"), "OtherModQuest")
        second = block(action(140, text="HELM21", target=17) + action(30, 3, "OtherModWell"), "OtherModQuest")
        original = ('SC\n' + added + block(well_reward(target=17)) + second + 'SC\n').encode().replace(b"\n", b"\r\n")
        result = rewards.patch_reward("bcs:ddwell", original, ddwell_ref=100001)
        self.assertEqual(rewards.patch_reward("bcs:ddwell", result), result)
        self.assertTrue(rewards.own_patch("bcs:ddwell", result))
        self.assertEqual(rewards._script_original(result, rewards.resource_specs("bcs:ddwell")),
                         (original, 100001, True))
        old, _, _ = _blocks(original, 3, b"\r\n")
        new, _, _ = _blocks(result, 5, b"\r\n")
        self.assertEqual(new[0], old[0])
        spec = rewards.REWARDS["ddwell"]
        crlf = lambda text: text.encode().replace(b"\n", b"\r\n")
        marker = crlf(f'AC\n30OB\n{obj(17)}OB\n{obj()}OB\n{obj()}1 0 0 0 0"LOCALS{spec["recipient_local"]}" "" AC\n')
        for first, grant in ((1, crlf(action(140, text="helm21", target=17))),
                             (3, crlf(action(140, text="HELM21", target=17)))):
            unselected, selected = new[first], new[first + 1]
            self.assertIn(b'"GLOBALFLPR_QM2_27"', unselected)
            self.assertEqual(unselected.count(marker), 1)
            self.assertEqual(selected.count(marker), 1)
            self.assertIn(grant, unselected)
            self.assertNotIn(grant, selected)
            self.assertIn(b"GLOBALFLPR_XM2_27", selected)
        self.assertIn(crlf(action(151, 100001, target=1)), new[2])
        self.assertIn(crlf(action(151, 40850, target=1)), new[1])

    def test_the_books_two_scrolls_are_gated_apart_beside_the_rings_quest(self):
        specs = rewards.resource_specs("bcs:ppbook")
        self.assertEqual([(spec["route"], spec["quest"], spec["item"], spec["target_ids"]) for spec in specs],
                         [("ppbook", "ppbook", "scrl9b", ()), ("ppbook2", "ppbook", "scrl8z", ())])
        self.assertEqual(rewards.source_spec("original-source:bg2:mode2:item:262")["payload"],
                         {"resref": "scrl8z", "expiration": 0, "charges": [0, 0, 0], "flags": 0})
        self.assertNotIn("ppbook", rewards.TARGET_REWARDS.values())
        for installed in (True, False):
            with self.subTest(installed=installed):
                original = book_fixture(installed)
                result = self.synthetic_patch("ppbook", original)
                self.assertEqual(rewards._script_original(result, specs), (original, None, True))
                old, _, _ = _blocks(original, 3 if installed else 2, b"\n")
                new, _, _ = _blocks(result, 9 if installed else 5, b"\n")
                self.assertEqual(new[0], old[0])
                # Each block of the last page became four, one for each choice
                # of the scrolls' rows; the ring's actions and guard stay.
                for number, source in enumerate(old[1:]):
                    for index, choice in enumerate(product((False, True), repeat=2)):
                        branch = new[1 + number * 4 + index]
                        for spec, selected in zip(specs, choice):
                            self.assertIn(_global_guard(spec["selected_global"], selected, b"\n"), branch)
                            grant = action(140, text=spec["item"], target=17).encode()
                            self.assertEqual(branch.count(grant), 0 if selected else 1)
                            self.assertEqual(branch.count(b"GLOBAL" + spec["consumed_global"].encode()), selected)
                            self.assertEqual(branch.count(b"GLOBAL" + spec["event_global"].encode()), 1)
                            marker = (f'AC\n30OB\n{obj(17)}OB\n{obj()}OB\n{obj()}'
                                      f'1 0 0 0 0"LOCALS{spec["recipient_local"]}" "" AC\n').encode()
                            self.assertEqual(branch.count(marker), 1)
                        for kept in (b'"ring09"', b"GLOBALFLPR_QBOOK", b"GLOBALFLPR_XBOOK", b"GLOBALFLPR_EBOOK",
                                     action(151, 50658, target=1).encode()):
                            self.assertEqual(branch.count(kept), source.count(kept))
        # A scroll that the page gives twice leaves its route out; the other
        # scroll's route is prepared alone, and recognised again.
        twice = book_fixture().replace(BOOK_GIFTS.encode(),
                                       (BOOK_GIFTS + action(140, text="scrl9b", target=17)).encode())
        reasons = {"ppbook": "PPBOOK.BCS block 1 gives SCRL9B twice in one response"}
        self.assertEqual(rewards.route_reasons("ppbook", twice), reasons)
        partial = rewards.patch_reward("ppbook", twice)
        self.assertNotIn(b"FLPR_QM2_260", partial)
        self.assertIn(b"FLPR_QM2_262", partial)
        self.assertEqual(rewards.patch_reward("ppbook", partial), partial)
        self.assertTrue(rewards.own_patch("ppbook", partial))
        self.assertEqual(rewards.route_reasons("ppbook", partial), reasons)
        # Without the scrolls nothing is prepared.
        neither = book_fixture().replace(BOOK_GIFTS.encode(), b"")
        self.assertEqual(rewards.route_reasons("ppbook", neither),
                         {"ppbook": "PPBOOK.BCS has no block that gives SCRL9B",
                          "ppbook2": "PPBOOK.BCS has no block that gives SCRL8Z"})
        with self.assertRaisesRegex(ProfileError, "no block that gives SCRL9B"):
            rewards.patch_reward("ppbook", neither)
        # Another copy of one route's patch is not this adapter's.
        result = rewards.patch_reward("ppbook", book_fixture())
        tampered = result.replace(b"FLPR_RM2_262", b"FLPR_OTHERS", 1)
        with self.assertRaisesRegex(ProfileError, "carries a changed copy"):
            rewards.patch_reward("ppbook", tampered)
        # The script the patch was made from; a conversation is none.
        self.assertEqual(rewards.unpatched_script("ppbook", result), book_fixture())
        self.assertEqual(rewards.unpatched_script("bcs:ppbook", book_fixture(False)), book_fixture(False))
        with self.assertRaisesRegex(ProfileError, "carries a changed copy"):
            rewards.unpatched_script("ppbook", tampered)
        with self.assertRaisesRegex(ProfileError, "UHOGRE01.DLG is not a reward script"):
            rewards.unpatched_script("dlg:uhogre01", b"")

    def test_bg1_routes_have_their_own_flags_and_take_the_games_names(self):
        # Varci's payment: BG:EE names the shield SHLD07, EET SHLD07A (its rename lists).
        spec = rewards.source_spec("original-source:bg1:mode2:item:20")
        self.assertEqual(spec, rewards.target_spec("bg1:mode2:target:20"))
        self.assertEqual((spec["campaign"], spec["item"], spec["resource"], spec["target_ids"]),
                         ("bg1", "shld07", "dlg:varci", ("bg1:mode2:target:20",)))
        self.assertEqual([spec[key] for key in ("selected_global", "consumed_global", "event_global",
                                                 "recipient_local")],
                         ["FLPR_QB1_20", "FLPR_XB1_20", "FLPR_EB1_20", "FLPR_RB1_20"])
        names = EetNames({"itm": {"shld07": "shld07a"}, "cre": {}, "dlg": {}, "bcs": {}, "sto": {}}, True)
        for game, shown in ((None, "shld07"), ("bgee", "shld07"), ("eet", "shld07a")):
            with self.subTest(game=game):
                for found in (rewards.source_spec("original-source:bg1:mode2:item:20", game=game, names=names),
                              rewards.reward("varci", game=game, names=names),
                              rewards.resource_specs("dlg:varci", game=game, names=names)[0]):
                    self.assertEqual((found["item"], found["payload"]["resref"]), (shown, shown))
        # EET keeps the names of BG2's routes.
        self.assertEqual(rewards.source_spec("original-source:bg2:mode2:item:14", game="eet", names=names),
                         rewards.source_spec("original-source:bg2:mode2:item:14"))
        with self.assertRaisesRegex(ProfileError, "Unsupported extended reward route: x"):
            rewards.reward("x")

    def test_eets_varci_gives_shld07a_to_an_object_function_and_the_route_gates_it(self):
        eet = {"game": "eet", "names": EetNames({"itm": {"shld07": "shld07a"}, "cre": {}, "dlg": {}, "bcs": {},
                                                 "sto": {}}, True)}
        spec = rewards.resource_specs("dlg:varci", **eet)[0]
        original = dialogue_fixture(spec)
        self.assertIn(b'GiveItemCreate("shld07a",LastTalkedToBy(Myself),0,0,0)', original)
        # Without EET's names the route looks for BG:EE's shield and names that.
        self.assertEqual(rewards.route_reasons("dlg:varci", original),
                         {"varci": "VARCI.DLG has no reply that gives SHLD07"})
        self.assertEqual(rewards.route_reasons("dlg:varci", original, **eet), {})
        result = rewards.patch_reward("dlg:varci", original, **eet)
        self.assertEqual(rewards.patch_reward("dlg:varci", result, **eet), result)
        self.assertTrue(rewards.own_patch("dlg:varci", result, **eet))
        self.assertEqual(rewards.route_reasons("dlg:varci", result, **eet), {})
        actions = b"".join(_Dialogue(result, "varci").actions)
        self.assertIn(b'ActionOverride(LastTalkedToBy(Myself),SetGlobal("FLPR_RB1_20","LOCALS",1))', actions)
        self.assertIn(b'SetGlobal("FLPR_XB1_20","GLOBAL",1)', actions)
        self.assertIn(b'SetGlobal("FLPR_EB1_20","GLOBAL",1)', actions)
        triggers = b"".join(_Dialogue(result, "varci").triggers)
        self.assertIn(b'!Global("FLPR_QB1_20","GLOBAL",1)', triggers)

    def test_registry_exposes_authored_charges_and_separate_container_recipient(self):
        self.assertEqual(len(rewards.DIRECT_REWARDS), 11)
        spec = rewards.source_spec("original-source:bg2:mode2:item:28")
        self.assertEqual(spec["payload"], {"resref": "ring31", "expiration": 0, "charges": [1, 1, 1], "flags": 0})
        spec["payload"]["charges"][0] = 99
        self.assertEqual(rewards.target_spec("bg2:mode2:target:35")["payload"]["charges"], [1, 1, 1])
        container = rewards.target_spec("bg2:mode2:target:21")
        self.assertEqual(container["recipient"], "are:ar1512/container/0")
        self.assertIsNone(container["recipient_local"])
        self.assertEqual(rewards.target_spec("bg2:mode2:target:40")["recipient"], "Player1")
        temple = rewards.target_spec("bg2:mode2:target:48")
        self.assertEqual(temple["resources"], ("dlg:bhoisig", "dlg:bhnalla", "dlg:bharval"))
        demon = rewards.source_spec("original-source:bg2:mode2:item:65")
        silver = rewards.source_spec("original-source:bg2:mode2:item:44")
        self.assertEqual(demon["payload"]["charges"], [0, 3, 0])
        self.assertEqual(demon["recipient"], "Player1")
        self.assertEqual(demon["event_global"], silver["event_global"])
        self.assertEqual(demon["recipient_local"], silver["recipient_local"])
        self.assertNotEqual(demon["selected_global"], silver["selected_global"])
        for message, call in (("Unsupported extended reward source", lambda: rewards.source_spec("x")),
                              ("Unsupported extended reward recipient", lambda: rewards.target_spec("x")),
                              ("Unsupported carried reward", lambda: rewards.transfer_spec("x")),
                              ("Unsupported extended reward resource", lambda: rewards.resource_specs("dlg:x"))):
            with self.assertRaisesRegex(ProfileError, message):
                call()

    def check_pair(self, name, original, result):
        """Two routes whose grants share every granting reply: four campaign combinations each."""
        before, after = _Dialogue(original, name), _Dialogue(result, name)
        specs = rewards.resource_specs("dlg:" + name)
        self.assertEqual([spec["quest"] for spec in specs], [name, name])
        shared = set(specs[0]["branches"])
        self.assertEqual(set(specs[1]["branches"]), shared)
        for state in range(len(before.states)):
            old, new = outgoing(before, state), outgoing(after, state)
            ordinals = {ordinal for granting, ordinal in shared if granting == state}
            if not ordinals:
                self.assertEqual(new, old)
                continue
            self.assertEqual(len(new), len(old) + 3 * len(ordinals))
            cursor = 0
            for ordinal, transition in enumerate(old):
                if ordinal not in ordinals:
                    self.assertEqual(new[cursor], transition)
                    cursor += 1
                    continue
                flags, _, _, trigger, action_id = struct.unpack_from("<5I", transition)
                prior = before.triggers[trigger] if flags & 2 else b""
                original_actions = before.actions[action_id]
                branches, cursor = new[cursor:cursor + 4], cursor + 4
                for bits, branch in enumerate(branches):
                    condition, restored = prior, after.actions[struct.unpack_from("<I", branch, 16)[0]]
                    self.assertEqual(branch[4:12], transition[4:12])
                    self.assertEqual(branch[20:], transition[20:])
                    for spec, selected in zip(specs, (bool(bits & 2), bool(bits & 1))):
                        condition = guarded(condition,
                            f'{"" if selected else "!"}Global("{spec["selected_global"]}","GLOBAL",1)\r\n'.encode())
                        grant, recipient = grant_in(original_actions, spec)
                        marker = (b"ActionOverride(" + recipient +
                                  f',SetGlobal("{spec["recipient_local"]}","LOCALS",1))\r\n'.encode())
                        event = f'\r\nSetGlobal("{spec["event_global"]}","GLOBAL",1)'.encode()
                        self.assertEqual(restored.count(marker), 1)
                        self.assertEqual(restored.count(event), 1)
                        restored = restored.replace(marker, b"", 1).replace(event, b"", 1)
                        if selected:
                            self.assertNotIn(grant, restored)
                            consumed = f'SetGlobal("{spec["consumed_global"]}","GLOBAL",1)'.encode()
                            restored = restored.replace(consumed, grant, 1)
                        else:
                            self.assertIn(grant, restored)
                    # A treasury's handover follows the guards, in every variant.
                    for spec in specs:
                        if spec.get("handover"):
                            condition = guarded(condition, handover_line(spec))
                    self.assertEqual(after.triggers[struct.unpack_from("<I", branch, 12)[0]], condition)
                    self.assertEqual(restored, original_actions)
            self.assertEqual(cursor, len(new))

    def test_aran_two_grants_have_all_four_independent_campaign_combinations(self):
        original = dialogue_fixture(rewards.resource_specs("dlg:aran"))
        self.check_pair("aran", original, self.synthetic_patch("aran", original))

    def test_the_sahuagin_kings_reply_gates_the_bracer_and_the_treasury_rod_apart(self):
        # The rod is the treasury's container reward; the reply that creates
        # another one follows its selected flag. The rod's place is the
        # treasury, whose reward the reply hands to the one the king pays.
        rod = rewards.REWARDS["sahrod"]
        self.assertEqual((rod["item"], rod["source_ids"], rod["target_ids"], rod["authored_source_ids"]),
                         ("rods02", (), ("bg2:mode2:target:6",), ("original-source:bg2:mode2:item:7",)))
        self.assertEqual((rod["selected_global"], rod["consumed_global"]), ("FLPR_QM2_7", "FLPR_XM2_7"))
        self.assertEqual(rod["handover"], {"area": "ar2300", "script": "sahtreas"})
        self.assertEqual(handover_line(rod), b'EEex_LuaTrigger("return(FLPR_Handover==nil)or(FLPR_Handover('
                                             b'EEex_LuaTrigger_Object,6))")\r\n')
        for key in ("sahkng01", "sahrod"):
            self.assertEqual(rewards.REWARDS[key]["resources"], ("dlg:sahkng01", "bcs:sahtreas"))
        self.assertEqual(rewards.TARGET_REWARDS["bg2:mode2:target:6"], "sahrod")
        self.assertEqual(rewards.PHYSICAL_SOURCE_REWARDS, {("cre:udlesa", "cre-template", "sw1h39"): "udlesa",
                                                           ("are:ar2300", "container", "rods02"): "sahrod"})
        self.assertNotIn("original-source:bg2:mode2:item:7", rewards.SOURCE_REWARDS)
        specs = rewards.resource_specs("dlg:sahkng01")
        self.assertEqual([spec["route"] for spec in specs], ["sahkng01", "sahrod"])
        original = dialogue_fixture(specs)
        result = self.synthetic_patch("sahkng01", original)
        self.check_pair("sahkng01", original, result)
        for data in (original, result):
            self.assertTrue(rewards.gives_reward("dlg:sahkng01", data, "sahrod"))
        # Another mod's king gives no rod: the bracer's route stays and the
        # rod needs no route; the rod's place, whose reward no reply would
        # hand over, cannot be used (route_reasons).
        dialogue = _Dialogue(original, "sahkng01")
        dialogue.actions = [text.replace(b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0)\r\r\n', b"")
                            for text in dialogue.actions]
        without = dialogue.render()
        self.assertFalse(rewards.gives_reward("dlg:sahkng01", without, "sahrod"))
        self.assertEqual(rewards.route_reasons("dlg:sahkng01", without),
                         {"sahrod": "SAHKNG01.DLG has no reply that gives RODS02"})
        patched = rewards.patch_reward("dlg:sahkng01", without)
        self.assertNotIn(b"FLPR_QM2_7", b"".join(_Dialogue(patched, "sahkng01").triggers))
        self.assertNotIn(b"FLPR_Handover", patched)
        # One that only takes or tests the rod gives none either; one that
        # gives it another way does, and the route says why it cannot gate it.
        taking = with_actions(without, "sahkng01", {(80, 0): b'TakePartyItem("RODS02")\r\n'})
        self.assertFalse(rewards.gives_reward("dlg:sahkng01", taking, "sahrod"))
        for actions, reason in (
                (b'ActionOverride("sahpr2",GiveItemCreate("rods02",LastTalkedToBy,0,0,0))\r\n',
                 "SAHKNG01.DLG state 80 gives RODS02 through ActionOverride(), whose recipient the route cannot mark"),
                (b'CreateItem("rods02",0,0,0)\r\n',
                 "SAHKNG01.DLG state 80 also gives RODS02 with CreateItem(), which the route cannot gate"),
                (b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0\r\n',
                 "SAHKNG01.DLG state 80 names RODS02 in actions that cannot be read")):
            with self.subTest(actions=actions):
                other = with_actions(without, "sahkng01", {(80, 0): actions})
                self.assertTrue(rewards.gives_reward("dlg:sahkng01", other, "sahrod"))
                self.assertEqual(rewards.route_reasons("dlg:sahkng01", other)["sahrod"], reason)
        with self.assertRaisesRegex(ProfileError, "Unsupported extended reward route in SAHKNG01.DLG: aran"):
            rewards.gives_reward("dlg:sahkng01", original, "aran")

    def test_each_reply_that_pays_with_the_rod_hands_the_treasury_over_after_its_guards(self):
        specs = rewards.resource_specs("dlg:sahkng01")
        rod = next(spec for spec in specs if spec["route"] == "sahrod")
        line = handover_line(rod)
        original = dialogue_fixture(specs)
        result = rewards.patch_reward("dlg:sahkng01", original)
        # The recovery reads as the original does.
        self.assertEqual(rewards._logical(rewards._examine("dlg:sahkng01", result).original, "dlg:sahkng01"),
                         rewards._logical(original, "dlg:sahkng01"))
        handed = [index for index, text in enumerate(_Dialogue(result, "sahkng01").triggers) if line in text]
        self.assertEqual(len(handed), 4)
        # A missing, repeated, moved or other handover is a changed copy of the patch.
        first = handed[0]
        for label, change in (("missing", lambda text: text.replace(line, b"")),
                              ("twice", lambda text: text + line),
                              ("another place", lambda text: text.replace(b",6))", b",7))")),
                              ("before the guards", lambda text: line + text.replace(line, b""))):
            with self.subTest(label):
                dialogue = _Dialogue(result, "sahkng01")
                dialogue.triggers[first] = change(dialogue.triggers[first])
                tampered = dialogue.render()
                with self.assertRaisesRegex(ProfileError, "carries a changed copy of the reward route's own patch"):
                    rewards.patch_reward("dlg:sahkng01", tampered)
                self.assertEqual(set(rewards.route_reasons("dlg:sahkng01", tampered)), {"sahkng01", "sahrod"})
        # A mod whose king pays with the rod in another reply: that reply hands
        # the treasury over, the bracer's does not.
        dialogue = _Dialogue(original, "sahkng01")
        dialogue.actions = [text.replace(b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0)\r\r\n', b"")
                            for text in dialogue.actions]
        split = with_actions(dialogue.render(), "sahkng01",
                             {(80, 0): b'GiveItemCreate("rods02",LastTalkedToBy,0,0,0)\r\n'})
        self.assertEqual(rewards.route_reasons("dlg:sahkng01", split), {})
        patched = _Dialogue(self.synthetic_patch("sahkng01", split), "sahkng01")
        for state, hands in ((80, True), (81, False)):
            rows = outgoing(patched, state)
            self.assertEqual(len(rows), 2)
            for row in rows:
                trigger_id = struct.unpack_from("<I", row, 12)[0]
                self.assertEqual(patched.triggers[trigger_id].endswith(line), hands)
        # Only the route's own row of Item Randomiser's table is its place; a
        # BG1 row would name its campaign.
        for targets in ((), ("bg2:mode2:target:6", "bg2:mode2:target:58"), ("bg2:base:target:6",)):
            with self.subTest(targets=targets):
                with self.assertRaisesRegex(ProfileError, "The treasury's place changed"):
                    rewards._handover(dict(rod, target_ids=targets))
        bg1 = dict(rod, target_ids=("bg1:mode2:target:6",))
        self.assertEqual(rewards._handover(bg1), handover_line(bg1))
        self.assertIn(b"(EEex_LuaTrigger_Object,6,1))", handover_line(bg1))

    def check_job_end(self, original, result, naming=None):
        """The prince's dialogue as his route patches it: each reply that ends
        his job hands over what he carries before its own actions, and his
        departures after it wait for that; nothing else changes. Returns the
        replies that hand over."""
        spec = rewards.resource_specs("dlg:sahpr2", **(naming or {}))[0]
        transfer = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,59))")\r\n'
        before, after = _Dialogue(original, "sahpr2"), _Dialogue(result, "sahpr2")
        self.assertEqual(after.state_triggers, before.state_triggers)
        self.assertEqual(after.triggers, before.triggers)
        self.assertEqual(after.actions[:len(before.actions)], before.actions)
        self.assertEqual(len(after.states), len(before.states))
        handed = set()
        for index, state in enumerate(before.states):
            self.assertEqual(after.states[index][:4], state[:4])
            self.assertEqual(after.states[index][8:], state[8:])
            for ordinal, (old, new) in enumerate(zip(outgoing(before, index), outgoing(after, index))):
                if new == old:
                    continue
                self.assertEqual((new[:16], new[20:]), (old[:16], old[20:]))
                original_action = before.actions[struct.unpack_from("<I", old, 16)[0]]
                changed = after.actions[struct.unpack_from("<I", new, 16)[0]]
                self.assertNotIn(b"DestroySelf()", changed.replace(TRGENI_DEPARTURE, b"").splitlines())
                if changed.startswith(transfer):
                    handed.add((index, ordinal))
                    changed = changed[len(transfer):]
                self.assertNotIn(b"FLPR_Transfer", changed)
                self.assertEqual(changed.replace(TRGENI_DEPARTURE, b"DestroySelf()"), original_action)
        self.assertEqual(rewards._callback(spec, True) + b"\r\n", transfer)
        return handed

    def test_the_princes_reward_reply_hands_over_what_he_carries(self):
        # Item Randomiser's tadpoles2.fl gives its reward to SAHPR2.CRE and
        # adds a GiveItem to the reply that ends his job, which gives nothing
        # in the games. Here the prince carries the place's reward, and each
        # reply of his that ends his job hands it to the one he talks to.
        spec = rewards.target_spec("bg2:mode2:target:59")
        self.assertEqual(rewards.TARGET_REWARDS["bg2:mode2:target:59"], "sahpr2")
        self.assertEqual(rewards.RESOURCE_REWARDS["dlg:sahpr2"], ("sahpr2",))
        self.assertNotIn("sahpr2", rewards.SOURCE_REWARDS.values())
        self.assertEqual((spec["item"], spec["payload"], spec["source_ids"], spec["authored_source_ids"]),
                         (None, None, (), ()))
        self.assertEqual([spec[key] for key in ("selected_global", "consumed_global", "event_global",
                                                "recipient_local")], [None] * 4)
        self.assertEqual(spec["job_end"], ("villynatyjob", 2))
        self.assertEqual(spec["inventory_target"],
                         {"area": "ar2300", "creature_resource": "cre:sahpr2", "script_name": "sahpr2"})
        self.assertEqual((spec["recipient"], spec["grant_kind"]), ("LastTalkedToBy", "transfer"))
        self.assertEqual(rewards.reward("sahpr2", **EET), spec)
        with self.assertRaisesRegex(ProfileError, "Unsupported extended reward route in SAHPR2.DLG: sahpr2"):
            rewards.gives_reward("dlg:sahpr2", dialogue_fixture(spec), "sahpr2")
        # The game's reward reply beside another mod's: one that ends his job
        # through ActionOverride in other letter case and spacing, then leaves
        # in the state after it, and some that name the job but do not end it.
        game = ('AddXPObject(Player1,60500)\nSetGlobal("senityilijob","GLOBAL",2)\n'
                'SetGlobal("villynatyjob","GLOBAL",2)\nRunAwayFrom("sahkng01",15)')
        states = [
            [("True()", 'SetGlobal("villynatyjob","GLOBAL",1)', 1),
             ("", 'SetGlobal("villynatyjob","LOCALS",2)\nIncrementGlobal("VillynatyJob","Locals",1)', 1),
             ("", 'SetGlobal("villynatyjob2","GLOBAL",2)\nDestroySelf()', None)],
            [('Global("villynatyjob","GLOBAL",2)', 'DisplayStringHead(Myself,1)', None),
             ("", game, None)],
            [("", 'ActionOverride( "sahpr2" , SetGlobal( "VillynatyJob" , "global" , 2 ) )\nDestroySelf()', 3)],
            [("", 'DestroySelf()', None)],
        ]
        for beamdog in (True, False):
            with self.subTest(beamdog=beamdog):
                original = layout_dialogue("sahpr2", states, beamdog=beamdog)
                result = rewards.patch_reward("dlg:sahpr2", original)
                self.assertEqual(rewards.patch_reward("dlg:sahpr2", result), result)
                self.assertFalse(rewards.own_patch("dlg:sahpr2", original))
                self.assertTrue(rewards.own_patch("dlg:sahpr2", result))
                self.assertEqual(rewards.route_reasons("dlg:sahpr2", original), {})
                self.assertEqual(rewards.route_reasons("dlg:sahpr2", result), {})
                self.assertEqual(rewards._logical(rewards._examine("dlg:sahpr2", result).original, "dlg:sahpr2"),
                                 rewards._logical(original, "dlg:sahpr2"))
                self.assertEqual(self.check_job_end(original, result), {(1, 1), (2, 0)})
                after = _Dialogue(result, "sahpr2")
                # His departures after the transfer wait for it; the one of a
                # reply that never hands over stays instant.
                for state, ordinal, waits in ((0, 2, False), (2, 0, True), (3, 0, True)):
                    actions = after.actions[struct.unpack_from("<I", outgoing(after, state)[ordinal], 16)[0]]
                    self.assertEqual(TRGENI_DEPARTURE in actions, waits)
                # The transfer as the dialogue parser leaves it, in each Lua.
                actions = after.actions[struct.unpack_from("<I", outgoing(after, 1)[1], 16)[0]]
                chunks = re.findall(rb'EEex_LuaAction\("(return[^"\r\n]*)"\)', actions)
                self.assertEqual(chunks, [b"return(FLPR_Transfer(EEex_LuaAction_Object,59))"])
                for runtime in (Lua51, Lua53, LuaJIT):
                    lua = runtime(unpack_returned_tuples=True)
                    lua.execute('EEex_LuaAction_Object={};'
                                'function FLPR_Transfer(actor,target,campaign) '
                                'assert(actor==EEex_LuaAction_Object);seen={target,campaign};return -1 end')
                    self.assertEqual(lua.execute(chunks[0].decode()), -1)
                    self.assertEqual(list(lua.globals().seen.values()), [59])
                # Changed copies of the patch: another place, a transfer moved
                # into a reply that does not end his job or behind its actions,
                # twice, or gone from one of the replies.
                transfer = b'EEex_LuaAction("return(FLPR_Transfer(EEex_LuaAction_Object,59))")\r\n'
                job = struct.unpack_from("<I", outgoing(after, 1)[1], 16)[0]
                other = struct.unpack_from("<I", outgoing(after, 0)[0], 16)[0]
                for label, changes in (
                        ("another place", {job: after.actions[job].replace(b",59))", b",58))")}),
                        ("other reply", {job: after.actions[job][len(transfer):],
                                         other: transfer + after.actions[other]}),
                        ("behind", {job: after.actions[job][len(transfer):] + b"\r\n" + transfer}),
                        ("twice", {job: transfer + after.actions[job]}),
                        ("gone", {job: after.actions[job][len(transfer):]})):
                    with self.subTest(beamdog=beamdog, changed=label):
                        tampered = _Dialogue(result, "sahpr2")
                        for index, text in changes.items():
                            tampered.actions[index] = text
                        tampered = tampered.render()
                        with self.assertRaisesRegex(ProfileError,
                                                    "carries a changed copy of the reward route's own patch"):
                            rewards.patch_reward("dlg:sahpr2", tampered)
                        self.assertIn("changed copy", rewards.route_reasons("dlg:sahpr2", tampered)["sahpr2"])
        # A dialogue in which no reply ends his job has nothing to hand over;
        # one whose actions name the job unreadably, or change it another way
        # that could end it unseen, cannot be told apart.
        for changed, reason in (
                ({1: [("", 'SetGlobal("villynatyjob","GLOBAL",3)', None)], 2: [("", "NoAction()", None)]},
                 "SAHPR2.DLG has no reply that sets villynatyjob to 2"),
                ({2: [("", 'SetGlobal("villynatyjob","GLOBAL",2', None)]},
                 "SAHPR2.DLG state 2 names villynatyjob in actions that cannot be read"),
                ({2: [("", 'ActionOverride("sahpr2",SetGlobal("villynatyjob","GLOBAL",2)', None)]},
                 "SAHPR2.DLG state 2 names villynatyjob in actions that cannot be read"),
                ({2: [("", 'SetGlobal("villynatyjob",GLOBAL,2)', None)]},
                 "SAHPR2.DLG state 2 names villynatyjob in actions that cannot be read"),
                ({0: [("", 'IncrementGlobal("villynatyjob","GLOBAL",1)', 1)]},
                 "SAHPR2.DLG state 0 changes villynatyjob with IncrementGlobal(), which the route cannot follow"),
                ({2: [("", 'ActionOverride("sahpr2",SetGlobalTimer("VILLYNATYJOB","global",2))', None)]},
                 "SAHPR2.DLG state 2 changes villynatyjob with SetGlobalTimer(), which the route cannot follow")):
            with self.subTest(reason=reason):
                variant = [changed.get(index, replies) for index, replies in enumerate(states)]
                data = layout_dialogue("sahpr2", variant)
                self.assertEqual(rewards.route_reasons("dlg:sahpr2", data), {"sahpr2": reason})
                with self.assertRaisesRegex(ProfileError, re.escape(reason)):
                    rewards.patch_reward("dlg:sahpr2", data)
                self.assertFalse(rewards.own_patch("dlg:sahpr2", data))

    def check_destroys(self, original, result, specs):
        """Each block that destroys routes' items becomes one block per
        combination of those routes' flags, in route order; where a route is
        selected, NoAction() stands in for each of its item's destroys."""
        before, after = script_blocks(original), script_blocks(result)
        newline = before.newline
        self.assertEqual((after.newline, after.prefix, after.suffix), (newline, before.prefix, before.suffix))
        expected = []
        for block in before.blocks:
            head, conditions, responses = block_parts(block, newline)
            actions = block_actions(responses, newline)
            destroys = {spec["route"]: [action for action in actions if action.opcode == 169 and
                                        action.strings[0].lower() == spec["item"].encode()] for spec in specs}
            gating = [spec for spec in specs if destroys[spec["route"]]]
            for choice in product((False, True), repeat=len(gating)):
                edits = sorted((action.start, action.end) for spec, selected in zip(gating, choice) if selected
                               for action in destroys[spec["route"]])
                changed, at = b"", 0
                for start, end in edits:
                    changed += responses[at:start] + NO_ACTION.replace("\n", newline.decode()).encode()
                    at = end
                expected.append(head + b"CO" + newline + conditions +
                                b"".join(_global_guard(spec["selected_global"], selected, newline)
                                         for spec, selected in zip(gating, choice)) +
                                b"CO" + newline + changed + responses[at:])
        self.assertEqual(list(after.blocks), expected)

    def test_the_treasury_destroys_the_kings_items_only_while_his_grants_are_unchanged(self):
        specs = rewards.resource_specs("bcs:sahtreas")
        self.assertEqual([(spec["route"], spec["item"], spec["selected_global"]) for spec in specs],
                         [("sahkng01", "brac19", "FLPR_QM2_57"), ("sahrod", "rods02", "FLPR_QM2_7")])
        self.assertEqual(rewards.DESTROY_GATES, {"bcs:sahtreas": ("sahkng01", "sahrod")})
        # Another mod's block, and one that only tests the rod, stay as found.
        other = book_block([trigger(16399, 1, "GLOBALOtherMod")], action(30, 2, "GLOBALOtherMod"))
        testing = book_block([trigger(16481, 0, "RODS02")], action(30, 3, "GLOBALOtherMod"))
        original = treasury_fixture(other, book_block([KING_PAID], KING_DESTROYS), testing)
        gated = "".join(book_block([KING_PAID, route_guard("FLPR_QM2_57", bracer), route_guard("FLPR_QM2_7", rod)],
                                   (NO_ACTION if rod else action(169, text="RODS02")) +
                                   (NO_ACTION if bracer else action(169, text="BRAC19")))
                        for bracer in (False, True) for rod in (False, True))
        result = self.synthetic_patch("sahtreas", original)
        self.assertEqual(result, ('SC\n' + other + gated + testing + 'SC\n').encode())
        self.check_destroys(original, result, specs)
        self.assertEqual(rewards._examine("bcs:sahtreas", result).original, original)
        # In CRLF lines too.
        self.assertEqual(self.synthetic_patch("sahtreas", original.replace(b"\n", b"\r\n")),
                         result.replace(b"\n", b"\r\n"))
        # By meaning: a lowercase name, a destroy through ActionOverride(), one
        # in each alternative response and one in another block are gated alike.
        for label, blocks in (
                ("lowercase", (book_block([KING_PAID], action(169, text="rods02") + action(169, text="brac19")),)),
                ("other actor", (book_block([KING_PAID], action(169, text="BRAC19", actor=21) +
                                            action(30, 1, "GLOBALOtherMod")),)),
                ("alternatives", (alternatives(action(169, text="RODS02"), action(169, text="RODS02") +
                                               action(30, 1, "GLOBALOtherMod"), condition="GLOBALsahkngjob"),)),
                ("two blocks", (book_block([KING_PAID], action(169, text="RODS02")),
                                book_block([trigger(16399, 3, "GLOBALsahkngjob")], action(169, text="BRAC19"))))):
            with self.subTest(label):
                variant = treasury_fixture(*blocks)
                self.check_destroys(variant, self.synthetic_patch("sahtreas", variant), specs)
        # A script that destroys neither item needs no gate.
        plain = treasury_fixture(other, testing)
        self.assertEqual(rewards.patch_reward("bcs:sahtreas", plain), plain)
        self.assertFalse(rewards.own_patch("bcs:sahtreas", plain))
        self.assertEqual(rewards.route_reasons("bcs:sahtreas", plain), {})

    def test_the_treasurys_destroyed_items_are_read_by_meaning(self):
        # The treasury's script runs with every AI update of its container,
        # so any reward of a name it destroys could be destroyed there.
        self.assertEqual(rewards.destroyed_items("bcs:sahtreas", treasury_fixture()), {"rods02", "brac19"})
        patched = rewards.patch_reward("bcs:sahtreas", treasury_fixture())
        self.assertEqual(rewards.destroyed_items("SAHTREAS.BCS", patched), {"rods02", "brac19"})
        other = book_block([trigger(16399, 1, "GLOBALOtherMod")],
                           action(169, text="Sw1h01", actor=21) + action(30, 2, "GLOBALOtherMod"))
        for label, script, expected in (
                ("another mod's destroy", treasury_fixture(book_block([KING_PAID], KING_DESTROYS), other),
                 {"rods02", "brac19", "sw1h01"}),
                ("crlf", treasury_fixture(other).replace(b"\n", b"\r\n"), {"sw1h01"}),
                ("alternatives", treasury_fixture(alternatives(action(169, text="RODS02"), action(30, 1, "GLOBALX"),
                                                               condition="GLOBALsahkngjob")), {"rods02"}),
                ("tests only", treasury_fixture(book_block([trigger(16481, 0, "RODS02")], action(30, 3, "GLOBALX"))),
                 set()),
                ("no blocks", b"SC\nSC\n", set())):
            with self.subTest(label):
                self.assertEqual(rewards.destroyed_items("bcs:sahtreas", script), expected)
        unreadable = treasury_fixture().replace(b"169OB", b"169XX", 1)
        with self.assertRaisesRegex(ProfileError, "SAHTREAS.BCS cannot be read as a compiled script"):
            rewards.destroyed_items("bcs:sahtreas", unreadable)

    def test_a_treasury_that_could_take_a_moved_reward_otherwise_leaves_its_route_out(self):
        reason = ("SAHTREAS.BCS block 0 uses RODS02 in an action other than DestroyItem(), "
                  "which the route cannot gate")
        # GiveItem() from the treasury: the rod's route stays out, the bracer's is gated.
        giving = treasury_fixture(book_block([KING_PAID], KING_DESTROYS + action(15, text="RODS02", target=21)))
        self.assertEqual(rewards.route_reasons("bcs:sahtreas", giving), {"sahrod": reason})
        result = rewards.patch_reward("bcs:sahtreas", giving)
        self.assertEqual(result, treasury_fixture(*(
            book_block([KING_PAID, route_guard("FLPR_QM2_57", bracer)],
                       action(169, text="RODS02") + (NO_ACTION if bracer else action(169, text="BRAC19")) +
                       action(15, text="RODS02", target=21)) for bracer in (False, True))))
        self.assertTrue(rewards.own_patch("bcs:sahtreas", result))
        self.assertEqual(rewards.patch_reward("bcs:sahtreas", result), result)
        self.assertEqual(rewards.route_reasons("bcs:sahtreas", result), {"sahrod": reason})
        # Both items so used, or a script that cannot be read: neither route.
        both = treasury_fixture(book_block([KING_PAID], action(15, text="RODS02", target=21) +
                                           action(15, text="BRAC19", target=21)))
        self.assertEqual(rewards.route_reasons("bcs:sahtreas", both),
                         {"sahrod": reason, "sahkng01": reason.replace("RODS02", "BRAC19")})
        with self.assertRaisesRegex(ProfileError, "BRAC19 in an action other than DestroyItem"):
            rewards.patch_reward("bcs:sahtreas", both)
        unreadable = treasury_fixture().replace(b"169OB", b"169XX", 1)
        reasons = rewards.route_reasons("bcs:sahtreas", unreadable)
        self.assertEqual(set(reasons), {"sahkng01", "sahrod"})
        self.assertIn("SAHTREAS.BCS", reasons["sahrod"])
        # Our patch changed: a block dropped, a destroy back, a guard turned.
        result = rewards.patch_reward("bcs:sahtreas", treasury_fixture())
        blocks = list(script_blocks(result).blocks)
        for label, changed in (
                ("dropped", treasury_fixture(*(block.decode() for block in blocks[:3]))),
                ("destroy back", result.replace(NO_ACTION.encode(), action(169, text="RODS02").encode(), 1)),
                ("turned", result.replace(route_guard("FLPR_QM2_7", False).encode(),
                                          route_guard("FLPR_QM2_7", True).encode(), 1))):
            with self.subTest(label):
                with self.assertRaisesRegex(ProfileError, "SAHTREAS.BCS carries a changed copy"):
                    rewards.own_patch("bcs:sahtreas", changed)
                self.assertEqual(set(rewards.route_reasons("bcs:sahtreas", changed)), {"sahkng01", "sahrod"})

    def test_aran_route_left_out_alone_keeps_the_other_route(self):
        specs = rewards.resource_specs("dlg:aran")
        original = dialogue_fixture(specs)
        dialogue = _Dialogue(original, "aran")
        dialogue.actions = [text.replace(b'GiveItemCreate("amul21",LastTalkedToBy,0,0,0)\r\r\n', b"")
                            for text in dialogue.actions]
        without = dialogue.render()
        self.assertEqual(rewards.route_reasons("dlg:aran", without),
                         {"aran2": "ARAN.DLG has no reply that gives AMUL21"})
        result = rewards.patch_reward("dlg:aran", without)
        self.assertTrue(rewards.own_patch("dlg:aran", result))
        self.assertEqual(rewards.patch_reward("dlg:aran", result), result)
        self.assertEqual(rewards.route_reasons("dlg:aran", result),
                         {"aran2": "ARAN.DLG has no reply that gives AMUL21"})
        patched = b"".join(_Dialogue(result, "aran").triggers)
        self.assertIn(b"FLPR_QM2_47", patched)
        self.assertNotIn(b"FLPR_QM2_56", patched)
        # Neither recognised: each route names its own reason.
        dialogue.actions = [text.replace(b'GiveItemCreate("ring07",LastTalkedToBy,0,0,0)\r\r\n', b"")
                            for text in dialogue.actions]
        self.assertEqual(rewards.route_reasons("dlg:aran", dialogue.render()),
                         {"aran": "ARAN.DLG has no reply that gives RING07",
                          "aran2": "ARAN.DLG has no reply that gives AMUL21"})

    def check_text_graph(self, resource, original, result, refs, naming=None):
        name = resource.split(":")[1]
        specs = rewards.resource_specs(resource, **(naming or {}))
        # Grant branching is checked independently above. Check every text-route
        # edge against that graph, including unchanged external entry states.
        action_refs = {old: refs[old] for old in rewards.ACTION_TEXT_REFS.get(resource, ())}
        before = _Dialogue(rewards._patch_dialogue(original, specs, action_refs=action_refs), name)
        after = _Dialogue(result, name)
        flag = specs[0]["selected_global"]
        clones = {}
        for index, state in enumerate(before.states):
            # A reply's own text (flag 1) and its journal entry (flag 0x10).
            affected = struct.unpack_from("<I", state)[0] in refs or any(
                struct.unpack_from("<I", row)[0] & bit and struct.unpack_from("<I", row, offset)[0] in refs
                for row in outgoing(before, index) for bit, offset in ((1, 4), (0x10, 8)))
            if affected:
                clones[index] = len(before.states) + len(clones)
        self.assertEqual(len(after.states), len(before.states) + len(clones))
        self.assertEqual(after.actions, before.actions)
        for index, state in enumerate(before.states):
            destinations = [(index, False)] + ([(clones[index], True)] if index in clones else [])
            old_text, _, _, old_entry = struct.unpack("<4I", state)
            for destination, selected_text in destinations:
                new_text, _, _, entry = struct.unpack("<4I", after.states[destination])
                self.assertEqual(new_text, refs.get(old_text, old_text) if selected_text else old_text)
                if old_entry != 0xFFFFFFFF and index in clones:
                    guard = f'{"" if selected_text else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode()
                    self.assertEqual(after.state_triggers[entry], guarded(before.state_triggers[old_entry], guard))
                elif old_entry != 0xFFFFFFFF:
                    # Another start state keeps its condition; a gated pair before it moves it.
                    self.assertEqual(after.state_triggers[entry], before.state_triggers[old_entry])
                else:
                    self.assertEqual(entry, old_entry)
                new_rows, cursor = outgoing(after, destination), 0
                for row in outgoing(before, index):
                    flags, reply, journal, trigger, action_id, next_dlg, next_state = struct.unpack("<5I8sI", row)
                    internal = next_dlg.rstrip(b"\0").lower() == name.encode()
                    reroute = not flags & 8 and internal and next_state in clones
                    for selected_edge in ((False, True) if reroute else (False,)):
                        new = new_rows[cursor]; cursor += 1
                        nf, nr, nj, nt, na, nd, ns = struct.unpack("<5I8sI", new)
                        self.assertEqual((nf, nr, nj, na, nd), (flags | 2 if reroute else flags,
                            refs.get(reply, reply) if selected_text and flags & 1 else reply,
                            refs.get(journal, journal) if selected_text and flags & 0x10 else journal,
                            action_id, next_dlg))
                        self.assertEqual(ns, clones[next_state] if reroute and selected_edge else next_state)
                        if reroute:
                            prior = before.triggers[trigger] if flags & 2 else b""
                            guard = f'{"" if selected_edge else "!"}Global("{flag}","GLOBAL",1)\r\n'.encode()
                            self.assertEqual(after.triggers[nt], guarded(prior, guard))
                        else:
                            self.assertEqual(nt, trigger)
                self.assertEqual(cursor, len(new_rows))

        # The engine tries start states in the order of their conditions: each
        # gated pair takes the original condition's place.
        def start_order(dialogue):
            entries = [(struct.unpack_from("<I", state, 12)[0], index) for index, state in enumerate(dialogue.states)]
            return [index for entry, index in sorted(entries) if entry != 0xFFFFFFFF]

        self.assertEqual(start_order(after), [index for state in start_order(before)
                                              for index in ((state, clones[state]) if state in clones else (state,))])
        return clones

    def test_text_variants_preserve_original_routes_replies_and_verified_recovery(self):
        resources = [resource for resource in rewards.TEXT_REFS if resource.startswith("dlg:")]
        text, allocated = rewards.prepare_reward_texts(GameText(make_tlk(["Unchanged text"])), resources)
        self.assertEqual(rewards.prepare_reward_texts(text, resources), (text, allocated))
        self.assertIsNone(text.female)
        tlk = text.male
        self.assertEqual(_Tlk(tlk).text(0, set()), "Unchanged text")
        self.assertIn("<GABBER>", _Tlk(tlk).text(allocated["dlg:ppdjinn"][48995], set()))
        self.assertEqual(allocated["dlg:gorcamb"][63681], allocated["dlg:gorcamb"][64609])
        with self.assertRaisesRegex(ProfileError, "reward texts have no Spanish translation yet"):
            rewards.prepare_reward_texts(text, resources, "es_ES")
        for resource in resources:
            name = resource.split(":")[1]
            with self.subTest(resource=resource):
                d = _Dialogue(dialogue_fixture(rewards.resource_specs(resource)), name)
                originals = list(rewards.TEXT_REFS[resource])
                # Exercise both state text and reply replacement; production
                # locations are independently exercised by the cached resources.
                for index, old_ref in enumerate(originals):
                    if old_ref in rewards.ACTION_TEXT_REFS.get(resource, ()):
                        continue
                    if len(originals) > 1 and index == len(originals) - 1:
                        struct.pack_into("<I", outgoing(d, 0)[0], 4, old_ref)
                    else:
                        struct.pack_into("<I", d.states[index], 0, old_ref)
                d.state_triggers.append(b'NumTimesTalkedTo(0)\r\n')
                struct.pack_into("<I", d.states[0], 12, len(d.state_triggers) - 1)
                original = d.render()
                result = rewards.patch_reward(resource, original, refs=allocated[resource])
                self.check_text_graph(resource, original, result, allocated[resource])
                self.assertEqual(rewards._text_original(result, resource), (original, allocated[resource]))
                self.assertFalse(rewards.own_patch(resource, original))
                self.assertTrue(rewards.own_patch(resource, result))
                self.assertEqual(rewards.route_reasons(resource, result), {})
                self.assertEqual(rewards.patch_reward(resource, result), result)
                moved = {old: new + 1000 for old, new in allocated[resource].items()}
                revised = rewards.patch_reward(resource, result, refs=moved)
                self.assertEqual(rewards._text_original(revised, resource), (original, moved))
                self.check_text_graph(resource, original, revised, moved)
                # A changed reply or recovery record is not our patch any more.
                for damaged in (result.replace(b"FLPR_", b"OTHER", 1),
                                result[:-20] + bytes([result[-20] ^ 1]) + result[-19:]):
                    with self.assertRaisesRegex(ProfileError, "carries a changed copy"):
                        rewards.own_patch(resource, damaged)
                    self.assertIn("changed copy", rewards.route_reasons(resource, damaged)[name])
                with self.assertRaisesRegex(ProfileError, "allocated"):
                    rewards.patch_reward(resource, original)
        self.assertEqual(rewards.target_spec("bg2:mode2:target:22")["required_transfers"], ("scyarryl",))
        self.assertEqual(rewards.REWARDS["sctelwyn"]["event_global"], "FLPR_EA_SCULPTOR")
        self.assertEqual(rewards.REWARDS["scsain"]["event_global"], "FLPR_EA_SCULPTOR")

    def test_bg1_texts_take_the_games_numbers_and_a_journal_entry_keeps_its_title(self):
        # EET numbers BG:EE's texts 200000 higher; BG2's keep their numbers in every game.
        for game in (None, "bgee"):
            self.assertEqual(rewards.text_refs("dlg:aldeth", game), {5740: 1649, 26828: 1650})
            self.assertEqual(rewards.journal_refs("dlg:aldeth", game), (26828,))
        self.assertEqual(rewards.text_refs("dlg:aldeth", "eet"), {205740: 1649, 226828: 1650})
        self.assertEqual(rewards.journal_refs("dlg:aldeth", "eet"), (226828,))
        for game in ("eet", "bg2ee"):
            self.assertEqual(rewards.text_refs("dlg:jugjer01", game), rewards.TEXT_REFS["dlg:jugjer01"])
            self.assertEqual(rewards.journal_refs("dlg:jugjer01", game), ())
        # A quest entry's first line is its title; languages put one or two line breaks after it.
        self.assertEqual(rewards._titled("The Merchant League\nOld words.", "New words."),
                         "The Merchant League\nNew words.")
        self.assertEqual(rewards._titled("Liga Kupiecka\r\n\r\nStare.", "Nowe."), "Liga Kupiecka\r\n\r\nNowe.")
        for untitled in ("One line only", "Title\n \n", "\nNo title first", "", None):
            self.assertEqual(rewards._titled(untitled, "New words."), "New words.")
        # The new entry has the title of the game's own entry, in each of its text files.
        male, female = [""] * 26829, [""] * 26829
        male[26828] = "The Merchant League Investigation\nAldeth gave me a sword."
        female[26828] = "The Merchant League Investigation (f)\nAldeth gave me a sword."
        male[5740], female[5740] = "Take this sword.", "Take this sword."
        text, refs = rewards.prepare_reward_texts(GameText(make_tlk(male), make_tlk(female)), ("dlg:aldeth",),
                                                  game="bgee")
        self.assertEqual(set(refs["dlg:aldeth"]), {5740, 26828})
        found = translations.lines("en_us", rewards.text_lines("dlg:aldeth"))
        thanks, entry = found["in-game.tra", 1649], found["in-game.tra", 1650]
        new_male, new_female = _Tlk(text.male), _Tlk(text.female)
        journal, words = refs["dlg:aldeth"][26828], refs["dlg:aldeth"][5740]
        self.assertEqual(new_male.text(journal, set()), "The Merchant League Investigation\n" + entry[0])
        self.assertEqual(new_female.text(journal, set()), "The Merchant League Investigation (f)\n" + entry[1])
        # His words are no journal entry: they need no title.
        self.assertEqual((new_male.text(words, set()), new_female.text(words, set())), thanks)
        # EET: the numbers EET gives these texts.
        _text, eet = rewards.prepare_reward_texts(GameText(make_tlk([])), ("dlg:aldeth",), game="eet")
        self.assertEqual(set(eet["dlg:aldeth"]), {205740, 226828})

    def test_journal_lua_files_the_new_entry_under_the_quests_of_the_one_it_replaces(self):
        lua = (b"createQuest( 87 )\r\n"
               b"createEntry    ( 87, -1, 26822, {}, nil )\r\n"
               b"createEntry    ( 87, -1, 26828, {}, nil )\r\n"
               b"createEntry    ( 87, -1, 126828, {}, nil )\r\n"
               b"\tcreateEntry(90,-1,26828,{1,2},nil)\r\n"
               b"createEntry    ( 91, -1, 268280, {}, nil )\r\n")
        texts = {"dlg:aldeth": {5740: 300000, 26828: 300001}}
        result = rewards.journal_lua(lua, texts)
        self.assertEqual(result, lua.replace(
            b"createEntry    ( 87, -1, 26828, {}, nil )\r\n",
            b"createEntry    ( 87, -1, 26828, {}, nil )\r\ncreateEntry    ( 87, -1, 300001, {}, nil )\r\n").replace(
            b"\tcreateEntry(90,-1,26828,{1,2},nil)\r\n",
            b"\tcreateEntry(90,-1,26828,{1,2},nil)\r\n\tcreateEntry(90,-1,300001,{1,2},nil)\r\n"))
        # A second export lists it once; an entry no quest lists stays unlisted.
        self.assertEqual(rewards.journal_lua(result, texts), result)
        unlisted = b"createEntry    ( 87, -1, 26822, {}, nil )\n"
        self.assertEqual(rewards.journal_lua(unlisted, texts), unlisted)
        # Texts other than journal entries and other routes' texts are not filed.
        self.assertEqual(rewards.journal_lua(lua, {"dlg:jugjer01": {33760: 300002, 33777: 300003}}), lua)
        # EET's numbers.
        eet = b"createEntry    ( 87, -1, 226828, {}, nil )\n"
        self.assertEqual(rewards.journal_lua(eet, {"dlg:aldeth": {205740: 400000, 226828: 400001}}, "eet"),
                         eet + b"createEntry    ( 87, -1, 400001, {}, nil )\n")

    def test_a_cloned_start_state_keeps_its_place_among_the_start_conditions(self):
        # The engine tries start states in the order of their conditions. The
        # djinn's reward state starts the dialogue; another mod's start state
        # comes after it and must stay after both of its variants.
        d = _Dialogue(dialogue_fixture(rewards.resource_specs("dlg:ppdjinn")), "ppdjinn")
        struct.pack_into("<I", d.states[0], 0, 48995)
        d.state_triggers += [b'NumTimesTalkedTo(0)\r\n', b'Global("OtherModDjinn","GLOBAL",1)\r\n']
        struct.pack_into("<I", d.states[0], 12, 0)
        struct.pack_into("<I", d.states[1], 12, 1)
        original = d.render()
        result = rewards.patch_reward("dlg:ppdjinn", original, refs={48995: 100001})
        after = _Dialogue(result, "ppdjinn")
        entries = {index: struct.unpack_from("<I", state, 12)[0] for index, state in enumerate(after.states)}
        clone = len(d.states)
        self.assertEqual(struct.unpack_from("<I", after.states[clone], 0)[0], 100001)
        self.assertEqual((entries[0], entries[clone], entries[1]), (0, 1, 2))
        self.assertEqual(after.state_triggers, [
            b'NumTimesTalkedTo(0)\r\n!Global("FLPR_QM2_24","GLOBAL",1)\r\n',
            b'NumTimesTalkedTo(0)\r\nGlobal("FLPR_QM2_24","GLOBAL",1)\r\n',
            b'Global("OtherModDjinn","GLOBAL",1)\r\n'])
        self.assertEqual(rewards._text_original(result, "dlg:ppdjinn"), (original, {48995: 100001}))
        self.assertEqual(rewards.patch_reward("dlg:ppdjinn", result), result)
        # Where every other start condition comes first, the gated pair is
        # appended, as earlier exports did.
        struct.pack_into("<I", d.states[0], 12, 1)
        struct.pack_into("<I", d.states[1], 12, 0)
        after = _Dialogue(rewards.patch_reward("dlg:ppdjinn", d.render(), refs={48995: 100001}), "ppdjinn")
        self.assertEqual(after.state_triggers[:2], d.state_triggers)
        self.assertEqual([struct.unpack_from("<I", after.states[index], 12)[0] for index in (1, 0, clone)], [0, 2, 3])

    def test_sculptor_transfer_is_source12_auxiliary_copy_and_preserves_ordinary_giveitem(self):
        spec = rewards.transfer_spec("scyarryl")
        self.assertEqual(spec["authored_source_ids"], ("original-source:bg2:mode2:item:12",))
        self.assertEqual(spec["source_ids"], ())
        self.assertEqual(rewards.SOURCE_REWARDS[spec["authored_source_ids"][0]], "bhoisig")
        # The export finds the copy in the scanned CRE; no game's bytes are pinned.
        self.assertEqual(spec["source_resource"], "cre:scyarryl")
        for pinned in ("physical_source_ids", "source_record", "source_resource_sha256"):
            self.assertNotIn(pinned, spec)
        self.assertEqual(spec["selected_global"], rewards.REWARDS["bhoisig"]["selected_global"])
        self.assertEqual(rewards.source_spec(spec["authored_source_ids"][0])["required_transfers"], ("scyarryl",))
        self.assertEqual(spec["event_global"], "FLPR_EA_SCULPTOR")
        self.assertEqual(rewards.target_spec("bg2:mode2:target:22")["resources"],
                         ("dlg:sctelwyn", "dlg:scsain", "dlg:scyarryl"))
        original = dialogue_fixture(spec)
        # Test the transfer branching itself independently of selected narrative.
        result = rewards._patch_dialogue(original, rewards.resource_specs("dlg:scyarryl"))
        self.check_dialogue("scyarryl", original, result)
        dlg = _Dialogue(result, "scyarryl")
        ordinary, selected = outgoing(dlg, 19)
        ordinary_action = dlg.actions[struct.unpack_from("<I", ordinary, 16)[0]]
        selected_action = dlg.actions[struct.unpack_from("<I", selected, 16)[0]]
        self.assertIn(b'GiveItem("blun20",LastTalkedToBy)', ordinary_action)
        self.assertNotIn(b'GiveItem("blun20"', selected_action)
        self.assertNotIn(b'GiveItemCreate("blun20"', selected_action)
        self.assertIn(b'SetGlobal("FLPR_XM2_12","GLOBAL",1)', selected_action)

    def test_carried_rewards_keep_sources_and_transfer_callbacks_before_later_actions(self):
        cases = (("trgeni01", 32, 32, "ar2014", "trgeni01"),
                 ("loutcapt", 39, 42, "ar0514", "Dennis"),
                 ("amcler01", 84, 85, "ar5500", "amcler01"),
                 ("jugjer01", 31, 31, "ar1103", "Jermien"))
        text, allocated = rewards.prepare_reward_texts(GameText(make_tlk(["Unchanged"])),
                             ("dlg:trgeni01", "dlg:amcler01"))
        self.assertEqual(_Tlk(text.male).text(allocated["dlg:amcler01"][74025], set()), "Please, accept this gift.")
        for name, source, target, area, script in cases:
            with self.subTest(resource=name):
                identity = f"original-source:bg2:mode2:item:{source}"
                spec = rewards.transfer_spec(name)
                self.assertEqual(spec, rewards.source_spec(identity))
                self.assertEqual(spec, rewards.target_spec(f"bg2:mode2:target:{target}"))
                self.assertEqual(spec["source_ids"], (identity,))
                self.assertEqual(spec["authored_source_ids"], (identity,))
                self.assertEqual(spec["required_transfers"], (name,))
                self.assertEqual(spec.get("replacement"), "blun05" if name == "trgeni01" else None)
                # The reward as the creature carries it: Jermien's sword keeps its daily charge.
                self.assertEqual(spec["payload"], {"resref": spec["item"], "expiration": 0, "flags": 0,
                                                   "charges": [1, 1, 0] if name == "jugjer01" else [0, 0, 0]})
                self.assertEqual(spec["inventory_target"], {"area": area, "creature_resource": "cre:" + name, "script_name": script})
                self.assertEqual(spec["source_resource"], "cre:" + name)
                for pinned in ("physical_source_ids", "source_record", "source_resource_sha256"):
                    self.assertNotIn(pinned, spec)
                self.assertEqual(spec["recipient"], "LastTalkedToBy")
                self.assertEqual(spec["grant_kind"], "transfer")
                original = dialogue_fixture(spec)
                action_refs = allocated.get("dlg:" + name) if name == "amcler01" else None
                result = rewards._patch_dialogue(original, rewards.resource_specs("dlg:" + name), action_refs=action_refs)
                self.check_dialogue(name, original, result, action_refs)
                for action_bytes in _Dialogue(result, name).actions:
                    self.assertNotIn(f'GiveItemCreate("{spec["item"]}"'.encode(), action_bytes)

    def test_trgeni_terminal_departure_waits_for_transfer_and_upgrades_previous_patch(self):
        resource = "dlg:trgeni01"
        spec = rewards.resource_specs(resource)[0]
        dialogue = _Dialogue(dialogue_fixture(spec), "trgeni01")
        struct.pack_into("<I", dialogue.states[35], 0, 43514)
        original = dialogue.render()
        refs = {43514: 100001}
        legacy = rewards._patch_text_dialogue(original, resource, refs,
                                              defer_departure=False, numeric_target=False)
        self.assertEqual(rewards._text_original(legacy, resource), (original, refs))
        self.assertTrue(rewards.own_patch(resource, legacy))
        result = rewards.patch_reward(resource, legacy)
        previous = rewards._patch_text_dialogue(original, resource, refs, numeric_target=False)
        self.assertEqual(rewards.patch_reward(resource, previous), result)
        self.assertNotEqual(result, legacy)
        self.assertEqual(result, rewards.patch_reward(resource, original, refs=refs))
        self.assertEqual(rewards.patch_reward(resource, result), result)
        self.assertEqual(rewards._text_original(result, resource), (original, refs))
        branches = rewards._patch_dialogue(original, (spec,))
        self.check_dialogue("trgeni01", original, branches)
        self.check_text_graph(resource, original, result, refs)
        for state in (37, 38):
            before = _Dialogue(legacy, "trgeni01")
            old = before.actions[struct.unpack_from("<I", outgoing(before, state)[0], 16)[0]]
            after = _Dialogue(result, "trgeni01")
            changed = after.actions[struct.unpack_from("<I", outgoing(after, state)[0], 16)[0]]
            self.assertEqual(old, trgeni_terminal_actions().encode() + b"\r\n")
            self.assertEqual(changed.replace(TRGENI_DEPARTURE, b"DestroySelf()"), old)
        # The departure that waits is the speaker's own, after the replies that
        # hand the reward over; an unreachable DestroySelf stays instant.
        unreached = _Dialogue(original, "trgeni01")
        struct.pack_into("<I", outgoing(unreached, 36)[0], 28, 2)
        unreached = unreached.render()
        after = _Dialogue(rewards._patch_dialogue(unreached, (spec,)), "trgeni01")
        for state in (37, 38):
            changed = after.actions[struct.unpack_from("<I", outgoing(after, state)[0], 16)[0]]
            self.assertNotIn(TRGENI_DEPARTURE, changed)
        # An altered prior patch is not accepted merely because its original
        # body and recovery trailer still match.
        with self.assertRaisesRegex(ProfileError, "changed copy"):
            rewards.patch_reward(resource, legacy.replace(b"FLPR_Transfer(", b"FLPR_Tampered(", 1))

    def test_inventory_callback_survives_dialogue_parser_and_returns_lua_result(self):
        for name in ("trgeni01", "loutcapt", "amcler01", "jugjer01", "udlesa", "aldeth", "nemphr"):
            spec = rewards.resource_specs("dlg:" + name)[0]
            original = dialogue_fixture(spec)
            patched = _Dialogue(rewards._patch_dialogue(original, (spec,)), name)
            target = int(spec["target_ids"][0].rsplit(":", 1)[1])
            # A BG1 row passes its campaign (1); M_FLPR.lua names the target from both.
            campaign = 1 if spec["campaign"] == "bg1" else None
            chunks = []
            for state, _ordinal in spec["branches"]:
                for row in outgoing(patched, state):
                    actions = patched.actions[struct.unpack_from("<I", row, 16)[0]]
                    chunks.extend(re.findall(rb'EEex_LuaAction\("(return[^"\r\n]*)"\)', actions))
            self.assertEqual(len(chunks), len(spec["branches"]) * 2)
            for runtime in (Lua51, Lua53, LuaJIT):
                with self.subTest(resource=name, runtime=runtime.__module__):
                    lua = runtime(unpack_returned_tuples=True)
                    lua.execute('EEex_LuaAction_Object={};calls=0;result=0;'
                                'function FLPR_Transfer(actor,target,campaign) '
                                'assert(actor==EEex_LuaAction_Object);calls=calls+1;'
                                'seen_target=target;seen_campaign=campaign;return result end')
                    for chunk in chunks:
                        passed = f"{target},1" if campaign else f"{target}"
                        self.assertEqual(chunk, f'return(FLPR_Transfer(EEex_LuaAction_Object,{passed}))'.encode())
                        self.assertNotIn(b" ", chunk)
                        self.assertNotIn(b":", chunk)
                        parsed = chunk.replace(b" ", b"").replace(b":", b"").decode()
                        self.assertEqual(lua.execute(parsed), 0)
                        self.assertEqual(lua.globals().seen_target, target)
                        self.assertEqual(lua.globals().seen_campaign, campaign)
                        lua.globals().result = -1
                        self.assertEqual(lua.execute(parsed), -1)
                        lua.globals().result = 0
                    self.assertEqual(lua.globals().calls, len(chunks) * 2)
            for target_ids in (("sod:mode2:target:32",), ("bg2:mode1:target:32",),
                               ("bg2:mode2:target:32extra",), ("bg2:mode2:target:32", "bg2:mode2:target:42")):
                invalid = dict(spec, target_ids=target_ids)
                with self.assertRaisesRegex(ProfileError, "transfer target changed"):
                    rewards._patch_dialogue(original, (invalid,))

    def test_udlesa_physical_source_and_six_distinct_duel_continuations(self):
        spec = rewards.target_spec("bg2:mode2:target:45")
        self.assertEqual(rewards.PHYSICAL_SOURCE_REWARDS[("cre:udlesa", "cre-template", "sw1h39")], "udlesa")
        self.assertNotIn("original-source:bg2:mode2:item:42", rewards.SOURCE_REWARDS)
        self.assertNotIn("udlesa", rewards.TRANSFER_SPECS)
        self.assertNotIn("required_transfers", spec)
        self.assertEqual(spec["source_ids"], ())
        self.assertEqual(spec["authored_source_ids"], ("original-source:bg2:mode2:item:42",))
        self.assertEqual(spec["replacement"], "dwsw1h02")
        self.assertEqual(spec["source_resource"], "cre:udlesa")
        self.assertEqual(spec["resource"], "dlg:udlesa")
        self.assertEqual(spec["inventory_target"], {"area": "ar2202", "creature_resource": "cre:udlesa", "script_name": "udlesa"})
        for pinned in ("physical_source_ids", "source_record", "source_resource_sha256"):
            self.assertNotIn(pinned, spec)
        self.assertEqual(spec["selected_global"], "FLPR_QM2_42")
        self.assertEqual(spec["consumed_global"], "FLPR_XM2_42")
        self.assertEqual(spec["branches"], tuple((state, 0) for state in range(6, 12)))
        dialogue = _Dialogue(dialogue_fixture(spec), "udlesa")
        for state in range(6, 12):
            action_id = struct.unpack_from("<I", outgoing(dialogue, state)[0], 16)[0]
            following = (f'SetGlobal("WaitingToDuel","GLOBAL",2)\r\n'
                         f'SetGlobal("DuelStarts","AR2202",3)\r\n'
                         f'ActionOverride(Player{state - 5},JumpToPoint([608.1123]))\r\n').encode()
            dialogue.actions[action_id] = dialogue.actions[action_id].replace(b"Wait(1)", following + b"Wait(1)")
        original = dialogue.render()
        result = rewards._patch_dialogue(original, rewards.resource_specs("dlg:udlesa"))
        self.check_dialogue("udlesa", original, result)
        after = _Dialogue(result, "udlesa")
        for state in range(6, 12):
            for transition in outgoing(after, state):
                actions = after.actions[struct.unpack_from("<I", transition, 16)[0]]
                self.assertIn(f'ActionOverride(Player{state - 5},JumpToPoint([608.1123]))'.encode(), actions)
                self.assertLess(actions.index(b"FLPR_Transfer("), actions.index(b'SetGlobal("WaitingToDuel"'))
                self.assertNotIn(b'GiveItemCreate("sw1h39"', actions)
        scan = ROOT / "work/original-preset-runtime-20260929/scan-with-spells/world.json"
        if scan.exists():
            world = json.loads(scan.read_text(encoding="utf-8"))
            target = next(row for row in world["targets"] if row["id"] == "cre:udlesa")
            self.assertEqual(target["selector"]["script_name"], spec["inventory_target"]["script_name"])

    def test_well_uses_appended_original_text_only_for_selected_source_and_reexports(self):
        tlk = make_tlk(["Existing unrelated text", "Existing helmet narrative"])
        updated, ref = rewards.prepare_ddwell_text(GameText(tlk))
        self.assertEqual(ref, 2)
        self.assertEqual(rewards.prepare_ddwell_text(updated), (updated, ref))
        text = _Tlk(updated.male)
        self.assertEqual(text.text(1, set()), "Existing helmet narrative")
        self.assertIn("You pull an item from the murky water", text.text(ref, set()))
        original = script_fixture("ddwell")
        specs = rewards.resource_specs("bcs:ddwell")
        result = rewards.patch_reward("ddwell", original, ddwell_ref=ref)
        self.assertEqual(rewards.patch_reward("ddwell", result), result)
        self.assertEqual(rewards._script_original(result, specs), (original, ref, True))
        self.assertEqual(rewards._script_original(original, specs), (original, None, False))
        changed_ref = rewards.patch_reward("ddwell", result, ddwell_ref=ref + 1)
        self.assertEqual(rewards._script_original(changed_ref, specs), (original, ref + 1, True))
        self.assertEqual(rewards.patch_reward("bcs:ddwell", result, refs={40850: ref + 1}), changed_ref)
        self.assertIn(action(151, 40850, target=1).encode(), result)
        self.assertIn(action(151, ref, target=1).encode(), result)
        with self.assertRaisesRegex(ProfileError, "changed copy"):
            rewards.patch_reward("ddwell", result.replace(b"AR1202DragonWell", b"AR1202DragonGone", 1))
        with self.assertRaisesRegex(ProfileError, "allocated"):
            rewards.patch_reward("ddwell", original)
        with self.assertRaisesRegex(ProfileError, "allocated"):
            rewards.patch_reward("bcs:ddwell", original, refs={40849: ref})

    def test_reviewed_eet_resources_when_available_patch_exactly_and_reexport(self):
        cache = ROOT / "work/original-preset-runtime-20260929/mode2-resources"
        if not cache.exists():
            self.skipTest("Read-only installed-resource cache is not distributed")
        self.assertEqual(set(REVIEWED_EET), set(rewards.RESOURCE_REWARDS))
        for resource in rewards.RESOURCE_REWARDS:
            kind, name = resource.split(":")
            # EET numbers BG1's texts (Aldeth's) 200000 higher than BG:EE does.
            _text, allocated = rewards.prepare_reward_texts(GameText(make_tlk([])), (resource,), game="eet")
            with self.subTest(resource=name):
                original = (cache / (name + "." + kind)).read_bytes()
                reviewed, patched = REVIEWED_EET[resource]
                self.assertEqual(hashlib.sha256(original).hexdigest(), reviewed)
                result = rewards.patch_reward(resource, original, refs=allocated.get(resource), **EET)
                self.assertEqual(hashlib.sha256(result).hexdigest(), patched)
                self.assertEqual(rewards.patch_reward(resource, result, **EET), result)
                self.assertEqual(rewards.route_reasons(resource, original, **EET), {})
                self.assertEqual(rewards.route_reasons(resource, result, **EET), {})
                self.assertFalse(rewards.own_patch(resource, original, **EET))
                self.assertTrue(rewards.own_patch(resource, result, **EET))
                if kind == "dlg" and resource in rewards.TEXT_REFS:
                    self.assertEqual(rewards._text_original(result, resource, **EET), (original, allocated[resource]))
                    clones = self.check_text_graph(resource, original, result, allocated[resource], naming=EET)
                    if name in ("trgeni01", "amcler01", "jugjer01", "udlesa", "aldeth"):
                        action_refs = allocated[resource] if name == "amcler01" else None
                        branches = rewards._patch_dialogue(original, rewards.resource_specs(resource, **EET),
                                                           action_refs=action_refs)
                        self.check_dialogue(name, original, branches, action_refs, naming=EET)
                        # Jermien names his sword when he offers it and when he hands it over;
                        # Aldeth's thanks and the journal entry of his reply name his.
                        self.assertEqual(set(clones), {"trgeni01": {35}, "amcler01": set(), "jugjer01": {17, 23},
                                                       "udlesa": {6}, "aldeth": {27}}[name])
                        if name == "aldeth":
                            dlg = _Dialogue(result, name)
                            self.assertEqual(struct.unpack_from("<I", dlg.states[clones[27]])[0],
                                             allocated[resource][205740])
                            self.assertEqual({struct.unpack_from("<I", row, 8)[0] for row in outgoing(dlg, clones[27])},
                                             {allocated[resource][226828]})
                            self.assertEqual({struct.unpack_from("<I", row, 8)[0] for row in outgoing(dlg, 27)},
                                             {226828})
                        if name == "udlesa":
                            dlg = _Dialogue(branches, name)
                            for state in range(6, 12):
                                for row in outgoing(dlg, state):
                                    actions = dlg.actions[struct.unpack_from("<I", row, 16)[0]]
                                    self.assertIn(f'ActionOverride(Player{state - 5},JumpToPoint([608.1123]))'.encode(), actions)
                                    self.assertLess(actions.index(b"FLPR_Transfer("), actions.index(b'SetGlobal("WaitingToDuel"'))
                    if name in ("sctelwyn", "scsain", "scyarryl"):
                        self.assertEqual(set(clones), {19})
                        # External temple-messenger entry states stay in place.
                        self.assertEqual(_Dialogue(original, name).states[27][:4], _Dialogue(result, name).states[27][:4])
                    if name == "ppdjinn":
                        dlg = _Dialogue(result, name)
                        self.assertEqual(len(dlg.states), 2)
                        for state in dlg.states:
                            entry = struct.unpack_from("<I", state, 12)[0]
                            self.assertIn(b"NumTimesTalkedTo(0)", dlg.state_triggers[entry])
                        self.assertIn(b'ReallyForceSpell(Myself,DRYAD_TELEPORT)', b"".join(dlg.actions))
                elif resource in rewards.DESTROY_GATES:
                    self.check_destroys(original, result, rewards.resource_specs(resource, **EET))
                    self.assertEqual(len(script_blocks(result).blocks), 4)
                elif kind == "bcs":
                    self.assertEqual(rewards._script_original(result, rewards.resource_specs(resource, **EET))[0],
                                     original)
                elif name in ("aran", "sahkng01"):
                    self.check_pair(name, original, result)
                elif name == "sahpr2":
                    self.assertEqual(self.check_job_end(original, result), {(5, 0)})
                else:
                    self.check_dialogue(name, original, result, naming=EET)


if __name__ == "__main__":
    unittest.main()
