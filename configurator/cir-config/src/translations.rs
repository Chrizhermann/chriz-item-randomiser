//! Item Randomiser's texts in the game's language (the reference's
//! `translations.py`).
//!
//! The game's language directory (`lang/<code>`, the scan's "language")
//! names the translation folder, `languages/<folder>/`. Translations are
//! written in their language's Windows code page; when CIR was installed in
//! that language, WeiDU converted the folder to UTF-8 in place and marked it
//! with `fl#utf8.mrk` (cir.tp2's HANDLE_CHARSETS). English is plain ASCII and
//! never converted. On the Enhanced Editions, cir.tp2 loads
//! `languages/bgee/<folder>/in-game.tra` after the general file, so its texts
//! win here too; those files are UTF-8 as written, since WeiDU loads them
//! unconverted.
//!
//! A text may carry a female wording (TRA `~text~ ~female text~`), which goes
//! to dialogf.tlk. A reward text the game's language has no translation for
//! is never replaced by the English one: the game shows its text in one
//! language, and a route that needs the text is left out with the reason
//! instead. The original options' texts (Kangaxx's conversation and ward
//! stone, the cursed disguises' descriptions) are English where the game's
//! language has no translation of them, so that these options work in every
//! game ([`option_lines`]).
//!
//! The configurator reads the translations it was built with
//! ([`Translations::Embedded`], the mod's `languages/` folder as written), so
//! they cannot change while it runs. The parity tests read a folder on disk
//! instead ([`Translations::Folder`]), as the reference's tests point
//! `LANGUAGES_DIR` at one.

use crate::error::{Error, ErrorKind, Result, MISSING_TRANSLATION};
use crate::quest_options::tra_variants;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// A game language directory's two-letter code to (language, translation
/// folder, code page). The folder names are WeiDU's, whose HANDLE_CHARSETS
/// infers the code page from them; the codes are the ones BG:EE, BG2:EE and
/// EET ship.
pub const LANGUAGES: &[(&str, (&str, &str, &str))] = &[
    ("cs", ("Czech", "czech", "cp1250")),
    ("de", ("German", "german", "cp1252")),
    ("en", ("English", "english", "utf-8")),
    ("es", ("Spanish", "spanish", "cp1252")),
    ("fr", ("French", "french", "cp1252")),
    ("hu", ("Hungarian", "hungarian", "cp1250")),
    ("it", ("Italian", "italian", "cp1252")),
    ("ja", ("Japanese", "japanese", "cp932")),
    ("ko", ("Korean", "korean", "cp949")),
    ("pl", ("Polish", "polish", "cp1250")),
    ("pt", ("Brazilian Portuguese", "portuguese", "cp1252")),
    ("ru", ("Russian", "russian", "cp1251")),
    ("tr", ("Turkish", "turkish", "cp1254")),
    ("uk", ("Ukrainian", "ukrainian", "cp1251")),
    ("zh", ("Chinese", "schinese", "cp936")),
];
pub const CONVERTED: &str = "fl#utf8.mrk";

/// (text, female wording) by TRA number.
pub type Texts = BTreeMap<i64, (String, String)>;
/// A (TRA file, number) line.
pub type Line = (String, i64);

/// (language, folder, code page) of a game language directory; `None` for an
/// unknown one.
pub fn language(code: &str) -> Option<(&'static str, &'static str, &'static str)> {
    let folded = crate::py::casefold(code);
    if folded == "default" {
        return None;
    }
    let short = folded.split('_').next().unwrap_or_default();
    LANGUAGES.iter().find(|(known, _)| *known == short).map(|(_, found)| *found)
}

/// The name translators know a code page by.
pub fn charset(encoding: &str) -> String {
    if encoding == "utf-8" {
        return "UTF-8".to_string();
    }
    if encoding.starts_with("cp125") {
        return format!("Windows-{}", &encoding[2..]);
    }
    match encoding {
        "cp932" => "Shift-JIS (Windows-932)",
        "cp949" => "Windows-949",
        "cp936" => "GBK (Windows-936)",
        _ => panic!("code page {encoding}"),
    }
    .to_string()
}

fn missing_translation(message: impl Into<String>) -> Error {
    Error::profile_subclass(MISSING_TRANSLATION, message)
}

/// Where the translations are read from.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Translations {
    /// The `languages/` folder built into the configurator.
    Embedded,
    /// A `languages` folder on disk (the reference's `LANGUAGES_DIR`).
    Folder(PathBuf),
}

impl Translations {
    /// The translations the library reads: the embedded ones, or with the
    /// `parity-overrides` feature the folder `CIR_PARITY_LANGUAGES` names (as
    /// the reference's tests patch `LANGUAGES_DIR`).
    pub fn current() -> Translations {
        #[cfg(feature = "parity-overrides")]
        if let Some(folder) = std::env::var_os("CIR_PARITY_LANGUAGES") {
            return Translations::Folder(PathBuf::from(folder));
        }
        Translations::Embedded
    }

    /// The file or folder `parts` below the languages folder, on disk.
    fn path(folder: &Path, parts: &[&str]) -> PathBuf {
        parts.iter().fold(folder.to_path_buf(), |path, part| path.join(part))
    }

    fn embedded(parts: &[&str]) -> String {
        format!("languages/{}", parts.join("/"))
    }

    fn is_file(&self, parts: &[&str]) -> bool {
        match self {
            Translations::Embedded => crate::data::file(&Translations::embedded(parts)).is_some(),
            Translations::Folder(folder) => Translations::path(folder, parts).is_file(),
        }
    }

    fn is_dir(&self, parts: &[&str]) -> bool {
        match self {
            Translations::Embedded => crate::data::folder(&Translations::embedded(parts)),
            Translations::Folder(folder) => Translations::path(folder, parts).is_dir(),
        }
    }

    /// `path.relative_to(LANGUAGES_DIR.parent).as_posix()`.
    fn shown(&self, parts: &[&str]) -> String {
        match self {
            Translations::Embedded => Translations::embedded(parts),
            Translations::Folder(folder) => match folder.file_name() {
                Some(name) => format!("{}/{}", name.to_string_lossy(), parts.join("/")),
                None => parts.join("/"),
            },
        }
    }

    fn read(&self, parts: &[&str], encoding: &str) -> Result<Texts> {
        if !self.is_file(parts) {
            return Ok(Texts::new());
        }
        let data = match self {
            Translations::Embedded => crate::data::file(&Translations::embedded(parts)).expect("embedded").to_vec(),
            Translations::Folder(folder) => {
                let path = Translations::path(folder, parts);
                std::fs::read(&path).map_err(|error| Error::os(&error, &path))?
            }
        };
        let Some(source) = crate::codepages::decode(&data, encoding) else {
            return Err(missing_translation(format!(
                "{} is not {} text, which this translation file must be",
                self.shown(parts),
                charset(encoding)
            )));
        };
        tra_variants(&source)
    }

    /// (text, female wording) of `languages/<folder>/<name>` for the game
    /// language `code`, with its Enhanced Edition texts; empty without a
    /// translation.
    pub fn texts(&self, code: &str, name: &str) -> Result<Texts> {
        let Some((_, folder, encoding)) = language(code) else {
            return Ok(Texts::new());
        };
        let converted = self.is_file(&[folder, CONVERTED]);
        let mut result = self.read(&[folder, name], if converted { "utf-8" } else { encoding })?;
        result.extend(self.read(&["bgee", folder, name], "utf-8")?);
        Ok(result)
    }

    /// (text, female wording) of the (file, number) lines `wanted` in the game
    /// language `code`.
    ///
    /// Fails with MissingTranslation and the reason when the language has no
    /// translation, or its translation lacks one of the lines.
    pub fn lines(&self, code: &str, wanted: &[Line]) -> Result<BTreeMap<Line, (String, String)>> {
        let Some((name, folder, _)) = language(code) else {
            if crate::py::casefold(code) == "default" {
                return Err(missing_translation(
                    "the game keeps its text outside a language directory, so CIR cannot tell which translation \
                     of Item Randomiser's reward texts it needs",
                ));
            }
            return Err(missing_translation(format!(
                "Item Randomiser's reward texts have no translation for the game language {code}"
            )));
        };
        if !self.is_dir(&[folder]) {
            return Err(missing_translation(format!("Item Randomiser's reward texts have no {name} translation yet")));
        }
        let mut loaded: BTreeMap<&str, Texts> = BTreeMap::new();
        let mut result = BTreeMap::new();
        for (file, number) in wanted {
            if !loaded.contains_key(file.as_str()) {
                loaded.insert(file, self.texts(code, file)?);
            }
            let Some(text) = loaded[file.as_str()].get(number) else {
                return Err(missing_translation(format!(
                    "Item Randomiser's {name} translation has no reward text @{number} in {folder}/{file}"
                )));
            };
            result.insert((file.clone(), *number), text.clone());
        }
        Ok(result)
    }

    /// (text, female wording) of an original option's lines `wanted` in the
    /// game language `code`, or the English ones where that language has no
    /// translation of all of them.
    ///
    /// One option's texts stay in one language. A translation file that is
    /// not in its code page counts as no translation, so the option still
    /// works.
    pub fn option_lines(&self, code: &str, wanted: &[Line]) -> Result<BTreeMap<Line, (String, String)>> {
        let mut loaded: BTreeMap<&str, Texts> = BTreeMap::new();
        for (file, _) in wanted {
            if !loaded.contains_key(file.as_str()) {
                match self.texts(code, file) {
                    Ok(texts) => loaded.insert(file, texts),
                    Err(error) if error.kind == ErrorKind::Profile && error.is(MISSING_TRANSLATION) => {
                        return self.lines("en_US", wanted)
                    }
                    Err(error) => return Err(error),
                };
            }
        }
        if !wanted.iter().all(|(file, number)| loaded[file.as_str()].contains_key(number)) {
            return self.lines("en_US", wanted);
        }
        Ok(wanted.iter().map(|(file, number)| ((file.clone(), *number), loaded[file.as_str()][number].clone())).collect())
    }

    /// Why the game language `code` cannot show the lines `wanted`; `None`
    /// when it can.
    pub fn missing(&self, code: &str, wanted: &[Line]) -> Result<Option<String>> {
        match self.lines(code, wanted) {
            Ok(_) => Ok(None),
            Err(error) if error.kind == ErrorKind::Profile && error.is(MISSING_TRANSLATION) => Ok(Some(error.message)),
            Err(error) => Err(error),
        }
    }

    /// The folders and files that decide the game language `code`'s texts,
    /// as far as they exist (a folder's identity covers the files it holds,
    /// see [`crate::resources::file_identity`], so a translation or marker
    /// that appears or goes away changes it too). None for the embedded
    /// translations, which cannot change.
    pub fn sources(&self, code: &str) -> Result<Vec<PathBuf>> {
        let Translations::Folder(root) = self else {
            return Ok(Vec::new());
        };
        let ee = root.join("bgee");
        let own: Vec<PathBuf> = language(code)
            .map_or_else(Vec::new, |(_, folder, _)| vec![root.join(folder), ee.join(folder)])
            .into_iter()
            .filter(|folder| folder.is_dir())
            .collect();
        let mut files = Vec::new();
        for folder in &own {
            let mut entries = Vec::new();
            for entry in std::fs::read_dir(folder).map_err(|error| Error::win(&error, folder))? {
                entries.push(entry.map_err(|error| Error::win(&error, folder))?.path());
            }
            sort_paths(&mut entries);
            files.extend(entries.into_iter().filter(|path| path.is_file()));
        }
        Ok([root.clone(), ee].into_iter().filter(|folder| folder.is_dir()).chain(own).chain(files).collect())
    }
}

/// `sorted()` of the paths in one folder: Windows paths compare by their
/// lowercased names, POSIX paths as they are.
fn sort_paths(paths: &mut [PathBuf]) {
    let name = |path: &Path| path.file_name().map_or_else(String::new, |name| name.to_string_lossy().into_owned());
    if cfg!(windows) {
        paths.sort_by_cached_key(|path| crate::py::lower(&name(path)));
    } else {
        paths.sort_by_cached_key(|path| name(path));
    }
}

/// [`Translations::texts`] of the current translations.
pub fn texts(code: &str, name: &str) -> Result<Texts> {
    Translations::current().texts(code, name)
}

/// [`Translations::lines`] of the current translations.
pub fn lines(code: &str, wanted: &[Line]) -> Result<BTreeMap<Line, (String, String)>> {
    Translations::current().lines(code, wanted)
}

/// [`Translations::option_lines`] of the current translations.
pub fn option_lines(code: &str, wanted: &[Line]) -> Result<BTreeMap<Line, (String, String)>> {
    Translations::current().option_lines(code, wanted)
}

/// [`Translations::missing`] of the current translations.
pub fn missing(code: &str, wanted: &[Line]) -> Result<Option<String>> {
    Translations::current().missing(code, wanted)
}

/// [`Translations::sources`] of the current translations.
pub fn sources(code: &str) -> Result<Vec<PathBuf>> {
    Translations::current().sources(code)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(file: &str, number: i64) -> Line {
        (file.to_string(), number)
    }

    #[test]
    fn shipped_languages_and_charsets() {
        assert_eq!(language("pl_PL"), Some(("Polish", "polish", "cp1250")));
        assert_eq!(language("DE_de"), Some(("German", "german", "cp1252")));
        assert_eq!(language("default"), None);
        assert_eq!(language("nb_NO"), None);
        assert_eq!(charset("cp1250"), "Windows-1250");
        assert_eq!(charset("cp932"), "Shift-JIS (Windows-932)");
    }

    #[test]
    fn embedded_translations_read_their_code_page() {
        let polish = Translations::Embedded.lines("pl_PL", &[line("in-game.tra", 101)]).unwrap();
        let (text, female) = &polish[&line("in-game.tra", 101)];
        assert!(text.contains("podniosłeś") && female.contains("podniosłaś"));
        assert_eq!(
            Translations::Embedded.missing("es_ES", &[line("in-game.tra", 101)]).unwrap().as_deref(),
            Some("Item Randomiser's reward texts have no Spanish translation yet")
        );
        assert!(Translations::Embedded.sources("pl_PL").unwrap().is_empty());
    }
}
