"""Stage names on existing isolated-fixture save/load buttons, without installing.

Only existing, exactly recognized button actions are named. The additional
polling widgets only admit remote requests; native clicks must be dispatched by
the QA runtime after rendering. Output is restricted to this checkout's work/
directory, never overwrites an existing file, and never modifies the input.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


STAGING_ROOT = Path(__file__).resolve().parents[1] / "work"


class MenuError(ValueError):
    pass


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Binding:
    menu: str
    name: str
    # The button's text; a tuple accepts each spelling (2.6.6 wraps popup texts
    # in t(...), 2.7.3 in getUiString(...)).
    text: str | tuple[str, ...]
    action: str
    text_lua: bool = False
    # A main-menu button the game may place elsewhere (BG2:EE's single-player
    # button is in START_MAIN, after the campaign logos): named where present.
    required: bool = True

    def accepts(self, texts: list[str]) -> bool:
        return len(texts) == 1 and texts[0] in ((self.text,) if isinstance(self.text, str) else self.text)


BINDINGS = (
    Binding("START", "FLPR_QA_SINGLE", "SINGLEPLAYER_BUTTON",
            "startEngine:SetEngineState(1) Infinity_TransitionMenu('START_SP')", required=False),
    Binding("START_SP", "FLPR_QA_START_LOAD", "LOAD_GAME_BUTTON",
            "Infinity_PopMenu(); startEngine:OnLoadGameButtonClick()"),
    Binding("ESC_MENU", "FLPR_QA_SAVE", "SAVE_GAME_BUTTON", "optionsScreen:SaveGame(0)"),
    Binding("ESC_MENU", "FLPR_QA_LOAD", "LOAD_GAME_BUTTON",
            "popup2Button(19531, 'LOAD_BUTTON', function() optionsScreen:LoadGame() end)"),
    Binding("LOAD", "FLPR_QA_LOAD_CONFIRM", "LOAD_BUTTON",
            "loadScreen:LoadGame(gameSaves.files[currentSave].fileName)"),
    Binding("SAVE", "FLPR_QA_SAVE_NEW", "SAVE_BUTTON",
            "currentSave = 0 Infinity_PushMenu( 'SAVE_NEWSAVE' );"),
    Binding("SAVE_NEWSAVE", "FLPR_QA_SAVE_CONFIRM", "SAVE_BUTTON", "completeSave()"),
    Binding("POPUP_TWOBUTTON", "FLPR_QA_POPUP_CONFIRM",
            ("t(Popup2Button.okText or 'DONE_BUTTON')", "getUiString(Popup2Button.okText or 'DONE_BUTTON')"),
            "Infinity_PopMenu('POPUP_TWOBUTTON') "
            "if (Popup2Button.okFunc) then Popup2Button.okFunc() end", True),
    Binding("ESC_MENU", "FLPR_QA_QUIT", "QUIT_GAME_BUTTON", """
        if e:IsTouchUI() then
            popup3Button(16456,
                'CANCEL_BUTTON', nil,
                'MAIN_MENU_BUTTON', function() optionsScreen:QuitGame() end,
                'SAVE_BUTTON', function() optionsScreen:SaveGame(0) end)
        else
            popup4Button(16456,
                'CANCEL_BUTTON', nil,
                'MAIN_MENU_BUTTON', function() optionsScreen:QuitGame() end,
                'QUIT_BUTTON', function() Infinity_ShutdownGame() end,
                'SAVE_BUTTON', function() optionsScreen:SaveGame(0) end)
        end
        """),
    Binding("POPUP_FOURBUTTON", "FLPR_QA_MAIN_MENU",
            ("t(Popup4Button.leftText)", "getUiString(Popup4Button.leftText)"),
            "Infinity_PopMenu() if (Popup4Button.leftFunc) then Popup4Button.leftFunc() end", True),
)
# Menus that get a console polling widget, so remote requests are admitted there.
# 2.7.3's save and load screens close ESC_MENU and draw no other polled menu, so
# they poll too; there tools/lab/M_FLPRQA.lua lets only the QA runner act (it
# queues button clicks) and keeps arbitrary remote requests pending.
POLLED_MENUS = ("ESC_MENU", "POPUP_TWOBUTTON", "POPUP_FOURBUTTON", "SAVE", "SAVE_NEWSAVE", "LOAD")


def tokenize(source: str) -> list[Token]:
    """Keep DSL strings/backtick Lua opaque, so their braces are never structure."""
    tokens = []
    position = 0
    while position < len(source):
        start = position
        char = source[position]
        if char.isspace() or char == "\ufeff":
            position += 1
        elif source.startswith("--", position):
            long_comment = re.match(r"--\[(=*)\[", source[position:])
            if long_comment:
                close = "]" + long_comment[1] + "]"
                end = source.find(close, position + len(long_comment[0]))
                if end < 0:
                    raise MenuError("Unterminated long comment")
                position = end + len(close)
            else:
                end = source.find("\n", position)
                position = len(source) if end < 0 else end
        elif char in "\"'`":
            delimiter = char
            position += 1
            while position < len(source):
                if source[position] == "\\" and delimiter != "`":
                    position += 2
                elif source[position] == delimiter:
                    position += 1
                    break
                else:
                    position += 1
            else:
                raise MenuError("Unterminated quoted string or Lua block")
            tokens.append(Token("lua" if delimiter == "`" else "string",
                                source[start + 1:position - 1], start, position))
        elif char in "{}":
            tokens.append(Token(char, char, start, start + 1))
            position += 1
        else:
            while (position < len(source) and not source[position].isspace()
                   and source[position] not in "{}\"'`"):
                position += 1
            tokens.append(Token("word", source[start:position], start, position))
    return tokens


def blocks(tokens: list[Token]) -> dict[int, int]:
    stack = []
    ends = {}
    for index, token in enumerate(tokens):
        if token.kind == "{":
            stack.append(index)
        elif token.kind == "}":
            if not stack:
                raise MenuError("Unmatched closing brace")
            ends[stack.pop()] = index
    if stack:
        raise MenuError("Unclosed menu block")
    return ends


def direct_tokens(tokens: list[Token], ends: dict[int, int], opening: int) -> list[Token]:
    direct = []
    index = opening + 1
    while index < ends[opening]:
        token = tokens[index]
        if token.kind == "{":
            index = ends[index] + 1
        else:
            direct.append(token)
            index += 1
    return direct


def property_values(tokens: list[Token], key: str, modifier: str | None = None) -> list[str]:
    values = []
    for index, token in enumerate(tokens):
        if token.kind != "word" or token.value.lower() != key.lower():
            continue
        value_index = index + 1
        if modifier is not None:
            if value_index >= len(tokens) or tokens[value_index].value.lower() != modifier:
                continue
            value_index += 1
        if value_index < len(tokens) and tokens[value_index].kind == "string":
            values.append(tokens[value_index].value)
    return values


def lua_signature(source: str) -> str:
    # These recognized actions contain no Lua long strings or comment-like
    # string values. Keep quoted text intact while ignoring actual comments.
    pieces = []
    pattern = r"--[^\r\n]*|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|\s+|."
    for match in re.finditer(pattern, source, re.DOTALL):
        piece = match[0]
        if not piece.startswith("--") and not piece.isspace():
            pieces.append(piece)
    return "".join(pieces)


def enhance(source: str) -> tuple[str, dict]:
    tokens = tokenize(source)
    ends = blocks(tokens)
    menus = {}
    duplicates = set()
    all_names = []
    for opening in ends:
        direct = direct_tokens(tokens, ends, opening)
        all_names.extend(property_values(direct, "name"))
        if opening and tokens[opening - 1].value.lower() == "menu":
            names = property_values(direct, "name")
            if len(names) != 1:
                raise MenuError("Menu must have exactly one quoted name")
            if names[0] in menus:
                duplicates.add(names[0])
            menus[names[0]] = opening
    # A game's own UI.MENU may define an unrelated menu twice (BG:EE 2.7.3 has
    # two MESSAGE_SCREEN menus); only the menus named here must be unambiguous.
    needed = {binding.menu for binding in BINDINGS} | set(POLLED_MENUS)
    if duplicates & needed:
        raise MenuError(f"Duplicate menu: {sorted(duplicates & needed)[0]}")
    newline = "\r\n" if "\r\n" in source else "\n"
    edits = []
    named = []
    unavailable = []
    for binding in BINDINGS:
        if binding.menu not in menus:
            if not binding.required:
                unavailable.append({"menu": binding.menu, "name": binding.name})
                continue
            raise MenuError(f"Required menu missing: {binding.menu}")
        opening = menus[binding.menu]
        matches = []
        index = opening + 1
        while index < ends[opening]:
            if tokens[index].kind == "{" and index > 0:
                if tokens[index - 1].value.lower() == "button":
                    direct = direct_tokens(tokens, ends, index)
                    texts = property_values(direct, "text", "lua" if binding.text_lua else None)
                    if binding.accepts(texts):
                        actions = property_values(direct, "action")
                        if len(actions) != 1 or lua_signature(actions[0]) != lua_signature(binding.action):
                            raise MenuError(f"Unexpected action for {binding.menu}/{binding.name}")
                        matches.append((index, direct))
                index = ends[index] + 1
            else:
                index += 1
        if not matches and not binding.required:
            unavailable.append({"menu": binding.menu, "name": binding.name})
            continue
        if len(matches) != 1:
            raise MenuError(f"Expected exactly one button for {binding.menu}/{binding.name}; found {len(matches)}")
        button, direct = matches[0]
        names = property_values(direct, "name")
        name_properties = sum(token.kind == "word" and token.value.lower() == "name" for token in direct)
        if name_properties != len(names):
            raise MenuError(f"Refusing unexpected unquoted name: {binding.menu}")
        if names and names != [binding.name]:
            raise MenuError(f"Refusing already-named button: {binding.menu}/{names}")
        if all_names.count(binding.name) != (1 if names else 0):
            raise MenuError(f"Name collision: {binding.name}")
        if not names:
            edits.append((tokens[button].end, newline + f'\t\tname "{binding.name}"'))
        named.append({"menu": binding.menu, "name": binding.name})
    added_polling = []
    for menu in POLLED_MENUS:
        if menu not in menus:
            raise MenuError(f"Required menu missing: {menu}")
        opening = menus[menu]
        existing_poll = False
        index = opening + 1
        while index < ends[opening]:
            if tokens[index].kind == "{":
                direct = direct_tokens(tokens, ends, index)
                if any(lua_signature(value) == "EEexRemote.Poll()"
                       for value in property_values(direct, "text", "lua")):
                    existing_poll = True
                index = ends[index] + 1
            else:
                index += 1
        if not existing_poll:
            widget = (f'\ttext{newline}\t{{{newline}\t\tarea 0 0 1 1{newline}'
                      f'\t\ttext lua "EEexRemote.Poll()"{newline}\t}}{newline}')
            edits.append((tokens[ends[opening]].start, widget))
            added_polling.append(menu)
    for position, insertion in sorted(edits, reverse=True):
        source = source[:position] + insertion + source[position:]
    return source, {"bindings": named, "unavailable": unavailable, "added_polling": added_polling,
                    "insertions": len(edits)}


def extract_menus(source: str, names: tuple[str, ...]) -> str:
    """Return complete requested menu blocks only; never global Lua or callbacks."""
    tokens = tokenize(source)
    ends = blocks(tokens)
    found = {}
    for opening in ends:
        if not opening or tokens[opening - 1].value.lower() != "menu":
            continue
        menu_names = property_values(direct_tokens(tokens, ends, opening), "name")
        if len(menu_names) == 1 and menu_names[0] in names:
            name = menu_names[0]
            if name in found:
                raise MenuError(f"Duplicate menu: {name}")
            found[name] = source[tokens[opening - 1].start:tokens[ends[opening]].end]
    if set(found) != set(names):
        raise MenuError("Requested extraction menu missing")
    newline = "\r\n" if "\r\n" in source else "\n"
    return (newline * 2).join(found[name] for name in names) + newline


def stage(input_path: Path, output_path: Path) -> dict:
    input_path = input_path.resolve(strict=True)
    output_path = output_path.resolve()
    if input_path == output_path:
        raise MenuError("Input is read-only; choose a separate staging output")
    if not output_path.is_relative_to(STAGING_ROOT.resolve()):
        raise MenuError("Output must remain beneath this checkout's work/ staging directory")
    if output_path.exists():
        raise MenuError("Refusing to overwrite an existing output")
    source = input_path.read_bytes().decode("utf-8")
    updated, report = enhance(source)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("xb") as output:
        output.write(updated.encode("utf-8"))
    return {"input": str(input_path), "output": str(output_path), **report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = stage(args.input, args.output)
    except (OSError, UnicodeError, MenuError) as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
