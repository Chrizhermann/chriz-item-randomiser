//! The game folder picker for a start without arguments: Windows' own
//! folder window (the common item dialog Explorer uses), elsewhere a
//! question on the terminal.

use std::path::{Path, PathBuf};

/// The folder the player picks, or None when the window is closed.
#[cfg(windows)]
pub fn pick_folder(title: &str, start: Option<&Path>) -> Result<Option<PathBuf>, String> {
    windows::pick_folder(title, start)
        .map_err(|code| format!("The folder window could not be shown (error 0x{:08X}).", code as u32))
}

/// The folder the player types, or None for an empty answer.
#[cfg(not(windows))]
pub fn pick_folder(title: &str, _start: Option<&Path>) -> Result<Option<PathBuf>, String> {
    use std::io::Write;
    print!("{title}: type its path, or just press Enter to stop: ");
    let _ = std::io::stdout().flush();
    let mut line = String::new();
    std::io::stdin().read_line(&mut line).map_err(|error| format!("Could not read the answer: {error}"))?;
    let line = line.trim();
    Ok((!line.is_empty()).then(|| PathBuf::from(line)))
}

/// IFileOpenDialog in folder mode through its COM interface, declared here
/// rather than pulling in a Windows bindings crate for four calls.
#[cfg(windows)]
mod windows {
    use std::ffi::{c_void, OsStr, OsString};
    use std::marker::PhantomData;
    use std::os::windows::ffi::{OsStrExt, OsStringExt};
    use std::path::{Path, PathBuf};
    use std::ptr::null_mut;

    pub type HResult = i32;

    #[repr(C)]
    struct Guid {
        data1: u32,
        data2: u16,
        data3: u16,
        data4: [u8; 8],
    }

    const CLSID_FILE_OPEN_DIALOG: Guid =
        Guid { data1: 0xDC1C_5A9C, data2: 0xE88A, data3: 0x4DDE, data4: [0xA5, 0xA1, 0x60, 0xF8, 0x2A, 0x20, 0xAE, 0xF7] };
    const IID_FILE_OPEN_DIALOG: Guid =
        Guid { data1: 0xD57C_7288, data2: 0xD4AD, data3: 0x4768, data4: [0xBE, 0x02, 0x9D, 0x96, 0x95, 0x32, 0xD9, 0x60] };
    const IID_SHELL_ITEM: Guid =
        Guid { data1: 0x4382_6D1E, data2: 0xE718, data3: 0x42EE, data4: [0xBC, 0x55, 0xA1, 0xE2, 0x61, 0xC3, 0x7B, 0xFE] };

    const COINIT_APARTMENTTHREADED: u32 = 0x2;
    const COINIT_DISABLE_OLE1DDE: u32 = 0x4;
    const CLSCTX_INPROC_SERVER: u32 = 0x1;
    pub const FOS_NOCHANGEDIR: u32 = 0x8;
    pub const FOS_PICKFOLDERS: u32 = 0x20;
    pub const FOS_FORCEFILESYSTEM: u32 = 0x40;
    pub const FOS_PATHMUSTEXIST: u32 = 0x800;
    const SIGDN_FILESYSPATH: i32 = 0x8005_8000_u32 as i32;
    /// HRESULT_FROM_WIN32(ERROR_CANCELLED): the player closed the window.
    const CANCELLED: HResult = 0x8007_04C7_u32 as i32;

    #[link(name = "ole32")]
    extern "system" {
        fn CoInitializeEx(reserved: *mut c_void, model: u32) -> HResult;
        fn CoUninitialize();
        fn CoCreateInstance(
            class: *const Guid,
            outer: *mut c_void,
            context: u32,
            interface: *const Guid,
            object: *mut *mut c_void,
        ) -> HResult;
        fn CoTaskMemFree(memory: *mut c_void);
    }

    #[link(name = "shell32")]
    extern "system" {
        fn SHCreateItemFromParsingName(
            path: *const u16,
            context: *mut c_void,
            interface: *const Guid,
            item: *mut *mut c_void,
        ) -> HResult;
    }

    /// IUnknown's methods, at the start of every interface's table.
    #[repr(C)]
    struct UnknownTable {
        query_interface: usize,
        add_ref: usize,
        release: unsafe extern "system" fn(*mut c_void) -> u32,
    }

    /// IFileOpenDialog's table (IModalWindow, then IFileDialog) up to
    /// GetResult, in shobjidl_core.h's order; unused methods are placeholders.
    #[repr(C)]
    pub struct DialogTable {
        unknown: UnknownTable,
        pub show: unsafe extern "system" fn(*mut c_void, *mut c_void) -> HResult,
        set_file_types: usize,
        set_file_type_index: usize,
        get_file_type_index: usize,
        advise: usize,
        unadvise: usize,
        pub set_options: unsafe extern "system" fn(*mut c_void, u32) -> HResult,
        pub get_options: unsafe extern "system" fn(*mut c_void, *mut u32) -> HResult,
        set_default_folder: usize,
        pub set_folder: unsafe extern "system" fn(*mut c_void, *mut c_void) -> HResult,
        pub get_folder: unsafe extern "system" fn(*mut c_void, *mut *mut c_void) -> HResult,
        get_current_selection: usize,
        set_file_name: usize,
        get_file_name: usize,
        pub set_title: unsafe extern "system" fn(*mut c_void, *const u16) -> HResult,
        pub set_ok_button_label: unsafe extern "system" fn(*mut c_void, *const u16) -> HResult,
        set_file_name_label: usize,
        pub get_result: unsafe extern "system" fn(*mut c_void, *mut *mut c_void) -> HResult,
    }

    /// IShellItem's table up to GetDisplayName.
    #[repr(C)]
    pub struct ItemTable {
        unknown: UnknownTable,
        bind_to_handler: usize,
        get_parent: usize,
        pub get_display_name: unsafe extern "system" fn(*mut c_void, i32, *mut *mut u16) -> HResult,
    }

    /// A COM interface pointer this code owns; released when dropped.
    pub struct Interface<T> {
        pub object: *mut c_void,
        table: PhantomData<T>,
    }

    impl<T> Interface<T> {
        /// SAFETY: `object` is a live interface whose table is `T`, and its
        /// reference passes to the new value.
        pub unsafe fn from_raw(object: *mut c_void) -> Interface<T> {
            Interface { object, table: PhantomData }
        }

        pub fn table(&self) -> &T {
            // SAFETY: a COM object starts with a pointer to its table.
            unsafe { &**(self.object as *const *const T) }
        }
    }

    impl<T> Drop for Interface<T> {
        fn drop(&mut self) {
            // SAFETY: every table starts with IUnknown's; the reference is ours.
            unsafe {
                let unknown = &**(self.object as *const *const UnknownTable);
                (unknown.release)(self.object);
            }
        }
    }

    /// COM on this thread in the single-threaded model the dialog needs.
    pub struct Apartment {
        entered: bool,
    }

    impl Apartment {
        pub fn enter() -> Apartment {
            // SAFETY: a plain call; success is balanced in drop.
            let result = unsafe { CoInitializeEx(null_mut(), COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE) };
            Apartment { entered: result >= 0 }
        }
    }

    impl Drop for Apartment {
        fn drop(&mut self) {
            if self.entered {
                // SAFETY: balances the successful CoInitializeEx.
                unsafe { CoUninitialize() }
            }
        }
    }

    fn wide(text: &OsStr) -> Vec<u16> {
        text.encode_wide().chain(Some(0)).collect()
    }

    pub fn check(result: HResult) -> Result<(), HResult> {
        if result < 0 {
            Err(result)
        } else {
            Ok(())
        }
    }

    fn shell_item(path: &Path) -> Result<Interface<ItemTable>, HResult> {
        let text = wide(path.as_os_str());
        let mut object = null_mut();
        // SAFETY: a NUL-terminated path, a valid interface id and out pointer.
        check(unsafe { SHCreateItemFromParsingName(text.as_ptr(), null_mut(), &IID_SHELL_ITEM, &mut object) })?;
        // SAFETY: the call succeeded, so `object` is an IShellItem we own.
        Ok(unsafe { Interface::from_raw(object) })
    }

    /// The file system path of a shell item.
    pub fn item_path(item: &Interface<ItemTable>) -> Result<PathBuf, HResult> {
        let mut name: *mut u16 = null_mut();
        // SAFETY: a live item and an out pointer.
        check(unsafe { (item.table().get_display_name)(item.object, SIGDN_FILESYSPATH, &mut name) })?;
        // SAFETY: the shell returns a NUL-terminated string the caller frees.
        unsafe {
            let length = (0..).take_while(|&index| *name.add(index) != 0).count();
            let path = OsString::from_wide(std::slice::from_raw_parts(name, length));
            CoTaskMemFree(name.cast());
            Ok(PathBuf::from(path))
        }
    }

    /// The folder window with its settings, not shown yet.
    pub fn prepared(title: &str, start: Option<&Path>) -> Result<Interface<DialogTable>, HResult> {
        let mut object = null_mut();
        // SAFETY: valid class and interface ids and an out pointer.
        check(unsafe {
            CoCreateInstance(&CLSID_FILE_OPEN_DIALOG, null_mut(), CLSCTX_INPROC_SERVER, &IID_FILE_OPEN_DIALOG, &mut object)
        })?;
        // SAFETY: the call succeeded, so `object` is an IFileOpenDialog we own.
        let dialog: Interface<DialogTable> = unsafe { Interface::from_raw(object) };
        let table = dialog.table();
        let title = wide(OsStr::new(title));
        let label = wide(OsStr::new("Use this folder"));
        let mut options = 0;
        // SAFETY: a live dialog; the strings are NUL-terminated and copied by
        // the dialog during the calls.
        unsafe {
            check((table.get_options)(dialog.object, &mut options))?;
            check((table.set_options)(
                dialog.object,
                options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR,
            ))?;
            // A missing title, button label or start folder is no reason to
            // refuse the window.
            (table.set_title)(dialog.object, title.as_ptr());
            (table.set_ok_button_label)(dialog.object, label.as_ptr());
            if let Some(item) = start.and_then(|start| shell_item(start).ok()) {
                (table.set_folder)(dialog.object, item.object);
            }
        }
        Ok(dialog)
    }

    pub fn pick_folder(title: &str, start: Option<&Path>) -> Result<Option<PathBuf>, HResult> {
        // Declared first, so it ends after the interfaces are released.
        let _apartment = Apartment::enter();
        let dialog = prepared(title, start)?;
        // SAFETY: a live dialog; no owner window.
        let shown = unsafe { (dialog.table().show)(dialog.object, null_mut()) };
        if shown == CANCELLED {
            return Ok(None);
        }
        check(shown)?;
        let mut object = null_mut();
        // SAFETY: the dialog closed with a choice; an out pointer.
        check(unsafe { (dialog.table().get_result)(dialog.object, &mut object) })?;
        // SAFETY: the call succeeded, so `object` is an IShellItem we own.
        let item: Interface<ItemTable> = unsafe { Interface::from_raw(object) };
        item_path(&item).map(Some)
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::windows::*;
    use std::ptr::null_mut;

    /// The interface tables match Windows' without showing the window: the
    /// folder mode and start folder read back, and before the window is
    /// shown it has no result rather than answering as another method.
    #[test]
    fn the_folder_window_keeps_its_settings() {
        let _apartment = Apartment::enter();
        let start = cir_config::paths::resolve(&std::env::temp_dir(), true).expect("the temporary folder");
        let dialog = prepared("Choose a folder", Some(&start)).expect("the folder window");
        let table = dialog.table();
        let mut options = 0;
        // SAFETY: a live dialog and out pointers.
        unsafe { check((table.get_options)(dialog.object, &mut options)).expect("its options") };
        let wanted = FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR;
        assert_eq!(options & wanted, wanted);
        let mut folder = null_mut();
        unsafe { check((table.get_folder)(dialog.object, &mut folder)).expect("its start folder") };
        let folder: Interface<ItemTable> = unsafe { Interface::from_raw(folder) };
        let path = item_path(&folder).expect("a file system path");
        assert!(path.as_os_str().eq_ignore_ascii_case(start.as_os_str()), "{path:?} != {start:?}");
        let mut result = null_mut();
        let answer = unsafe { (table.get_result)(dialog.object, &mut result) };
        assert!(answer < 0 && result.is_null(), "GetResult before Show: {answer:#x}");
    }
}
