//! The running game, its loader, WeiDU and mod installers, as Apply checks
//! them (the reference's `activation._game_or_installer_executables`).
//!
//! Windows' process snapshot, not WMI: other programs can keep WMI busy enough
//! that it cancels the query, and Apply then could not run at all.

use std::path::PathBuf;

/// The game, its loader, WeiDU or a mod installer (`setup-<mod>.exe`), by the
/// file name of the process in any case (the reference's `_GAME_OR_INSTALLER`).
#[cfg_attr(not(windows), allow(dead_code))]
fn is_game_or_installer(name: &str) -> bool {
    let name = name.to_ascii_lowercase();
    ["baldur", "infinityloader", "weidu"]
        .iter()
        .any(|program| name.strip_prefix(program).is_some_and(|rest| rest.is_empty() || rest.starts_with(".exe")))
        || (name.starts_with("setup-") && name.ends_with(".exe"))
}

/// The executable of each running game, loader, WeiDU or mod installer;
/// `None` where Windows does not tell it, such as for another user's process.
#[cfg(windows)]
pub(crate) fn game_or_installer_executables() -> std::io::Result<Vec<Option<PathBuf>>> {
    use std::ffi::c_void;
    use std::os::windows::ffi::OsStringExt;

    /// PROCESSENTRY32W
    #[repr(C)]
    struct ProcessEntry {
        size: u32,
        usage: u32,
        process: u32,
        heap: usize,
        module: u32,
        threads: u32,
        parent: u32,
        priority: i32,
        flags: u32,
        name: [u16; 260],
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn CreateToolhelp32Snapshot(flags: u32, process: u32) -> *mut c_void;
        fn Process32FirstW(snapshot: *mut c_void, entry: *mut ProcessEntry) -> i32;
        fn Process32NextW(snapshot: *mut c_void, entry: *mut ProcessEntry) -> i32;
        fn OpenProcess(access: u32, inherit: i32, process: u32) -> *mut c_void;
        fn QueryFullProcessImageNameW(process: *mut c_void, flags: u32, name: *mut u16, size: *mut u32) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }
    struct Handle(*mut c_void);
    impl Drop for Handle {
        fn drop(&mut self) {
            // SAFETY: the handle is open and owned by this value.
            unsafe { CloseHandle(self.0) };
        }
    }
    const SNAPSHOT_PROCESSES: u32 = 0x2;
    const QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const NO_MORE_FILES: i32 = 18;
    const INVALID_PARAMETER: i32 = 87;

    // SAFETY: no pointers are passed.
    let snapshot = unsafe { CreateToolhelp32Snapshot(SNAPSHOT_PROCESSES, 0) };
    if snapshot as isize == -1 {
        return Err(std::io::Error::last_os_error());
    }
    let snapshot = Handle(snapshot);
    let mut entry = ProcessEntry {
        size: std::mem::size_of::<ProcessEntry>() as u32,
        usage: 0,
        process: 0,
        heap: 0,
        module: 0,
        threads: 0,
        parent: 0,
        priority: 0,
        flags: 0,
        name: [0; 260],
    };
    let mut executables = Vec::new();
    // SAFETY: an open snapshot and a PROCESSENTRY32W whose size field is set.
    let mut listed = unsafe { Process32FirstW(snapshot.0, &mut entry) } != 0;
    while listed {
        let length = entry.name.iter().position(|&unit| unit == 0).unwrap_or(entry.name.len());
        if is_game_or_installer(&String::from_utf16_lossy(&entry.name[..length])) {
            // SAFETY: no pointers are passed.
            let process = unsafe { OpenProcess(QUERY_LIMITED_INFORMATION, 0, entry.process) };
            if !process.is_null() {
                let process = Handle(process);
                let mut path = vec![0u16; 32768];
                let mut size = path.len() as u32;
                // SAFETY: an open process handle and a buffer of `size` units.
                let known = unsafe { QueryFullProcessImageNameW(process.0, 0, path.as_mut_ptr(), &mut size) } != 0;
                executables.push(
                    (known && size > 0)
                        .then(|| PathBuf::from(std::ffi::OsString::from_wide(&path[..size as usize]))),
                );
            } else if std::io::Error::last_os_error().raw_os_error() != Some(INVALID_PARAMETER) {
                // One that ended since the snapshot is not listed.
                executables.push(None);
            }
        }
        // SAFETY: as for Process32FirstW.
        listed = unsafe { Process32NextW(snapshot.0, &mut entry) } != 0;
    }
    let error = std::io::Error::last_os_error();
    if error.raw_os_error() != Some(NO_MORE_FILES) {
        return Err(error);
    }
    Ok(executables)
}

#[cfg(not(windows))]
pub(crate) fn game_or_installer_executables() -> std::io::Result<Vec<Option<PathBuf>>> {
    Err(std::io::ErrorKind::Unsupported.into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_game_its_loader_weidu_and_mod_installers_in_any_case() {
        for name in [
            "Baldur.exe",
            "BALDUR.EXE",
            "baldur",
            "InfinityLoader.exe",
            "weidu.exe",
            "WeiDU.EXE",
            "setup-cir.exe",
            "Setup-Item_Randomiser.EXE",
            "setup-.exe",
            "Baldur.exe.old",
        ] {
            assert!(is_game_or_installer(name), "{name}");
        }
    }

    #[test]
    fn other_programs() {
        for name in [
            "",
            "BaldurGate.exe",
            "Baldur-launcher.exe",
            "baldur.ex",
            "setup.exe",
            "setup-cir.exe.log",
            "mysetup-cir.exe",
            "weidu64.exe",
            "cir-configurator.exe",
            "python.exe",
            "\u{130}nfinityLoader.exe",
        ] {
            assert!(!is_game_or_installer(name), "{name}");
        }
    }

    /// A copy of Windows' findstr, waiting for input, runs under each name.
    #[cfg(windows)]
    #[test]
    fn running_programs() {
        use crate::activation::require_closed;
        use crate::paths;
        use std::os::windows::process::CommandExt;
        use std::path::Path;
        use std::process::{Child, Command, Stdio};

        struct Scratch(PathBuf);
        impl Drop for Scratch {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        struct Running(Child);
        impl Drop for Running {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        let start = |path: &Path| {
            std::fs::create_dir_all(paths::parent(path)).unwrap();
            let findstr = Path::new(&std::env::var_os("SystemRoot").unwrap()).join("System32").join("findstr.exe");
            std::fs::copy(findstr, path).unwrap();
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            Running(
                Command::new(path)
                    .arg("x")
                    .stdin(Stdio::piped())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .creation_flags(CREATE_NO_WINDOW)
                    .spawn()
                    .unwrap(),
            )
        };
        let root = std::env::temp_dir()
            .join(format!("cir-processes-{}-{}", std::process::id(), crate::profiles::random_hex(8)));
        std::fs::create_dir_all(&root).unwrap();
        let scratch = Scratch(paths::resolve(&root, true).unwrap());
        let game = scratch.0.join("game");
        std::fs::create_dir_all(&game).unwrap();
        let open = "Close this test game and its installer before applying settings";

        let elsewhere = scratch.0.join("other game").join("Baldur.exe");
        let _other = start(&elsewhere);
        let listed = game_or_installer_executables().unwrap();
        assert!(listed.iter().flatten().any(|path| paths::resolve(path, false).unwrap() == elsewhere), "{listed:?}");
        require_closed(&game).unwrap();
        let installer = start(&game.join("setup-mod.exe"));
        assert_eq!(require_closed(&game).unwrap_err().message, open);
        drop(installer);
        require_closed(&game).unwrap();
    }
}
