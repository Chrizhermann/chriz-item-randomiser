"""Read the script text of dialogues and compiled scripts, without changing it.

Dialogue triggers and actions are text that the engine compiles when it runs
them: a sequence of calls such as ``GiveItemCreate("SHLD26",LastTalkedToBy,0,0,0)``,
whatever the spacing, line ends or letter case. Compiled BCS scripts are
blocks of conditions and responses. The reward adapters use these readers to
find what they change by its meaning, not by its position or exact bytes.
"""
from __future__ import annotations

from collections import namedtuple
from contextlib import contextmanager
import re

from .profiles import ProfileError

_SPACE = b" \t\r\n"
_NAME = re.compile(rb"!?[A-Za-z_][A-Za-z0-9_#]*")

# A call in dialogue text: its byte span, its name as written (with a leading
# "!" for a negated trigger) and the spans of its arguments, without the
# spaces around them.
Call = namedtuple("Call", "start end name args")


def calls(text: bytes, start: int = 0, end: int | None = None) -> list[Call] | None:
    """The top-level calls of text[start:end], or None if it is not a call sequence."""
    end = len(text) if end is None else end
    result, at = [], start
    while True:
        while at < end and text[at] in _SPACE:
            at += 1
        if at == end:
            return result
        name = _NAME.match(text, at, end)
        if name is None:
            return None
        cursor = name.end()
        while cursor < end and text[cursor] in b" \t":
            cursor += 1
        if cursor == end or text[cursor] != ord("("):
            return None
        args, depth, quoted, begin = [], 0, False, cursor + 1
        for position in range(cursor + 1, end):
            byte = text[position]
            if quoted:
                quoted = byte != ord('"')
            elif byte == ord('"'):
                quoted = True
            elif byte in b"([":
                depth += 1
            elif byte in b")]" and depth:
                depth -= 1
            elif byte == ord(")"):
                args.append(begin)
                args.append(position)
                break
            elif byte == ord(",") and not depth:
                args.append(begin)
                args.append(position)
                begin = position + 1
        else:
            return None
        spans = []
        for first, last in zip(args[::2], args[1::2]):
            while first < last and text[first] in _SPACE:
                first += 1
            while last > first and text[last - 1] in _SPACE:
                last -= 1
            spans.append((first, last))
        if len(spans) == 1 and spans[0][0] == spans[0][1]:
            spans = []  # No arguments, as in DestroySelf().
        result.append(Call(at, position + 1, text[at:name.end()], spans))
        at = position + 1


def argument(text: bytes, call: Call, index: int) -> bytes | None:
    """One argument of a call as written, or None when the call has fewer."""
    if index >= len(call.args):
        return None
    first, last = call.args[index]
    return text[first:last]


def string_argument(text: bytes, call: Call, index: int) -> bytes | None:
    """A quoted string argument without its quotes, or None."""
    value = argument(text, call, index)
    if value is None or len(value) < 2 or value[:1] != b'"' or value[-1:] != b'"':
        return None
    return value[1:-1]


def integer_argument(text: bytes, call: Call, index: int, default: int | None = None) -> int | None:
    """An integer argument; a missing one is <default>, a malformed one None."""
    value = argument(text, call, index)
    if value is None:
        return default
    return int(value) if re.fullmatch(rb"-?[0-9]+", value) else None


def named(call: Call, *names: str) -> bool:
    return call.name.decode("ascii").lower() in {name.lower() for name in names}


def joined(prior: bytes, line: bytes) -> bytes:
    """Append one trigger or action line to dialogue text.

    Beamdog's own compiled text often ends without a line break; never run the
    new line into the last existing call.
    """
    if prior and not prior[-1:].isspace():
        prior += b"\r\n"
    return prior + line


class ScriptError(ProfileError):
    """A compiled script these readers cannot read; see naming()."""


@contextmanager
def naming(label: str):
    """Name the resource in an error about reading its compiled script."""
    try:
        yield
    except ScriptError as exc:
        raise ProfileError(f"{label} cannot be read as a compiled script: {exc}") from None


# Compiled BCS. A block is CR, CO, its triggers (TR), CO, RS, its responses
# (RE, a weight, actions AC), RS, CR. Each action has an opcode, three objects
# (OB...OB) and a parameter line; each trigger has a parameter line and one
# object. Strings never span lines.
Script = namedtuple("Script", "newline prefix blocks suffix")
Action = namedtuple("Action", "start end opcode objects integer strings")
Trigger = namedtuple("Trigger", "start end opcode value negated strings")


def newline_of(data: bytes) -> bytes:
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    if b"\n" in data.replace(newline, b""):
        raise ScriptError("its line endings are mixed")
    return newline


def script_blocks(data: bytes) -> Script:
    """Split a compiled BCS into its blocks; each block keeps its CR lines."""
    newline = newline_of(data)
    tail = b"SC" + newline
    marker = b"CR" + newline + b"CO" + newline
    if not data.startswith(tail) or not data.endswith(tail) or len(data) < 2 * len(tail):
        raise ScriptError("it does not start and end with SC")
    starts = [match.start() for match in re.finditer(re.escape(marker), data)]
    end = len(data) - len(tail)
    if (starts and starts[0] != len(tail)) or (not starts and end != len(tail)):
        raise ScriptError("its first block does not follow SC")
    if starts and data[end - len(b"CR" + newline):end] != b"CR" + newline:
        raise ScriptError("its last block is not closed")
    blocks = [data[start:starts[index + 1] if index + 1 < len(starts) else end]
              for index, start in enumerate(starts)]
    return Script(newline, data[:len(tail)], blocks, data[end:])


def block_parts(block: bytes, newline: bytes) -> tuple[bytes, bytes, bytes]:
    """A block's head (CR CO), conditions and responses (from RS)."""
    boundary = b"CO" + newline
    parts = block.split(boundary)
    if len(parts) != 3:
        raise ScriptError("a block has an unknown structure")
    return parts[0], parts[1], parts[2]


def _object(newline: bytes) -> bytes:
    nl = re.escape(newline)
    return rb"OB" + nl + rb"([^\r\n]*)OB" + nl


def block_actions(responses: bytes, newline: bytes) -> list[Action]:
    """Every action of a block's responses, in order.

    The span starts at the action's AC, after a response weight that the
    compiler writes on the same line.
    """
    nl = re.escape(newline)
    pattern = re.compile(rb"AC" + nl + rb"([0-9]+)" + _object(newline) * 3 +
                         rb'(-?[0-9]+) -?[0-9]+ -?[0-9]+ -?[0-9]+ -?[0-9]+"([^"\r\n]*)" "([^"\r\n]*)" AC' + nl)
    result, at = [], 0
    while True:
        found = pattern.search(responses, at)
        if found is None:
            break
        result.append(Action(found.start(), found.end(), int(found[1]),
                             (found[2], found[3], found[4]), int(found[5]), (found[6], found[7])))
        at = found.end()
    # Each AC line opens an action; an unknown one hides what it does.
    if len(re.findall(rb"(?m)^(?:[0-9]+)?AC" + nl, responses)) != len(result):
        raise ScriptError("a block has an action of unknown structure")
    return result


def block_triggers(conditions: bytes, newline: bytes) -> list[Trigger]:
    """Every trigger of a block's conditions, in order."""
    nl = re.escape(newline)
    pattern = re.compile(rb"TR" + nl + rb'([0-9]+) (-?[0-9]+) ([0-9]+) -?[0-9]+ -?[0-9]+ '
                         rb'"([^"\r\n]*)" "([^"\r\n]*)" OB' + nl + rb"[^\r\n]*OB" + nl + rb"TR" + nl)
    result, at = [], 0
    while True:
        found = pattern.search(conditions, at)
        if found is None:
            break
        result.append(Trigger(found.start(), found.end(), int(found[1]), int(found[2]),
                              int(found[3]) & 1 == 1, (found[4], found[5])))
        at = found.end()
    if conditions.count(b"TR" + newline) != 2 * len(result):
        raise ScriptError("a block has a trigger of unknown structure")
    return result
