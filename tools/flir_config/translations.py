"""Item Randomiser's texts in the game's language.

The game's language directory (lang/<code>, the scan's "language") names the
translation folder, languages/<folder>/. Translations are written in their
language's Windows code page; when CIR was installed in that language, WeiDU
converted the folder to UTF-8 in place and marked it with fl#utf8.mrk
(cir.tp2's HANDLE_CHARSETS). English is plain ASCII and never converted. On
the Enhanced Editions, cir.tp2 loads languages/bgee/<folder>/in-game.tra after
the general file, so its texts win here too; those files are UTF-8 as written,
since WeiDU loads them unconverted.

A text may carry a female wording (TRA "~text~ ~female text~"), which goes to
dialogf.tlk. A text the game's language has no translation for is never
replaced by the English one: the game shows its text in one language, and a
route that needs the text is left out with the reason instead.
"""
from __future__ import annotations

from pathlib import Path

from .profiles import ProfileError
from .quest_options import ROOT, tra_variants

# A game language directory's two-letter code -> (language, translation folder,
# code page). The folder names are WeiDU's, whose HANDLE_CHARSETS infers the
# code page from them; the codes are the ones BG:EE, BG2:EE and EET ship.
LANGUAGES = {
    "cs": ("Czech", "czech", "cp1250"),
    "de": ("German", "german", "cp1252"),
    "en": ("English", "english", "utf-8"),
    "es": ("Spanish", "spanish", "cp1252"),
    "fr": ("French", "french", "cp1252"),
    "hu": ("Hungarian", "hungarian", "cp1250"),
    "it": ("Italian", "italian", "cp1252"),
    "ja": ("Japanese", "japanese", "cp932"),
    "ko": ("Korean", "korean", "cp949"),
    "pl": ("Polish", "polish", "cp1250"),
    "pt": ("Brazilian Portuguese", "portuguese", "cp1252"),
    "ru": ("Russian", "russian", "cp1251"),
    "tr": ("Turkish", "turkish", "cp1254"),
    "uk": ("Ukrainian", "ukrainian", "cp1251"),
    "zh": ("Chinese", "schinese", "cp936"),
}
CONVERTED = "fl#utf8.mrk"
LANGUAGES_DIR = ROOT / "languages"


class MissingTranslation(ProfileError):
    """The game's language has no translation of a text CIR needs."""


def language(code: str) -> tuple[str, str, str] | None:
    """(language, folder, code page) of a game language directory; None for an unknown one."""
    if code.casefold() == "default":
        return None
    return LANGUAGES.get(code.casefold().split("_")[0])


def charset(encoding: str) -> str:
    """The name translators know a code page by."""
    if encoding == "utf-8":
        return "UTF-8"
    if encoding.startswith("cp125"):
        return "Windows-" + encoding[2:]
    return {"cp932": "Shift-JIS (Windows-932)", "cp949": "Windows-949", "cp936": "GBK (Windows-936)"}[encoding]


def _read(path: Path, encoding: str) -> dict[int, tuple[str, str]]:
    if not path.is_file():
        return {}
    try:
        return tra_variants(path.read_bytes().decode(encoding))
    except UnicodeDecodeError:
        raise MissingTranslation(f"{path.relative_to(LANGUAGES_DIR.parent).as_posix()} is not "
                                 f"{charset(encoding)} text, which this translation file must be") from None


def texts(code: str, name: str) -> dict[int, tuple[str, str]]:
    """(text, female wording) of languages/<folder>/<name> for the game language <code>, with its
    Enhanced Edition texts; {} without a translation."""
    found = language(code)
    if found is None:
        return {}
    _, folder, encoding = found
    general = LANGUAGES_DIR / folder
    result = _read(general / name, "utf-8" if (general / CONVERTED).is_file() else encoding)
    result.update(_read(LANGUAGES_DIR / "bgee" / folder / name, "utf-8"))
    return result


def lines(code: str, wanted) -> dict[tuple[str, int], tuple[str, str]]:
    """(text, female wording) of the (file, number) lines <wanted> in the game language <code>.

    Raises MissingTranslation with the reason when the language has no
    translation, or its translation lacks one of the lines.
    """
    found = language(code)
    if found is None:
        if code.casefold() == "default":
            raise MissingTranslation("the game keeps its text outside a language directory, so CIR cannot tell "
                                     "which translation of Item Randomiser's reward texts it needs")
        raise MissingTranslation(f"Item Randomiser's reward texts have no translation for the game language {code}")
    name, folder, _ = found
    if not (LANGUAGES_DIR / folder).is_dir():
        raise MissingTranslation(f"Item Randomiser's reward texts have no {name} translation yet")
    loaded, result = {}, {}
    for file, number in wanted:
        if file not in loaded:
            loaded[file] = texts(code, file)
        if number not in loaded[file]:
            raise MissingTranslation(f"Item Randomiser's {name} translation has no reward text "
                                     f"@{number} in {folder}/{file}")
        result[file, number] = loaded[file][number]
    return result


def missing(code: str, wanted) -> str | None:
    """Why the game language <code> cannot show the lines <wanted>; None when it can."""
    try:
        lines(code, wanted)
    except MissingTranslation as exc:
        return str(exc)
    return None


def sources(code: str) -> list[Path]:
    """The folders and files that decide the game language <code>'s texts, as far as they exist.

    A folder's identity covers the files it holds (resources.file_identity),
    so a translation or marker that appears or goes away changes it too.
    """
    found = language(code)
    ee = LANGUAGES_DIR / "bgee"
    own = [] if found is None else [LANGUAGES_DIR / found[1], ee / found[1]]
    own = [folder for folder in own if folder.is_dir()]
    files = [path for folder in own for path in sorted(folder.iterdir()) if path.is_file()]
    return [folder for folder in (LANGUAGES_DIR, ee) if folder.is_dir()] + own + files
