//! Native RenderLock launcher: starts the bundled portable runtime without a
//! console window and shows a dialog when startup fails.

#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn log_path() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    base.join("RenderLock").join("logs").join("startup.log")
}

fn read_tail(path: &Path) -> String {
    match std::fs::read_to_string(path) {
        Ok(text) => {
            let chars: Vec<char> = text.chars().collect();
            let start = chars.len().saturating_sub(1800);
            chars[start..].iter().collect()
        }
        Err(_) => String::new(),
    }
}

fn message_box(title: &str, text: &str) {
    #[link(name = "user32")]
    unsafe extern "system" {
        fn MessageBoxW(hwnd: *mut core::ffi::c_void, text: *const u16, caption: *const u16, utype: u32) -> i32;
    }
    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            wide(text).as_ptr(),
            wide(title).as_ptr(),
            0x10, // MB_ICONERROR
        );
    }
}

fn fail(message: &str, log: Option<&Path>) {
    let text = match log {
        Some(path) => format!("{message}\n\nLog: {}", path.display()),
        None => message.to_string(),
    };
    message_box("RenderLock", &text);
}

fn main() -> ExitCode {
    let root = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."));
    let python = root.join("runtime").join("python.exe");
    let log = log_path();
    if let Some(dir) = log.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if !python.is_file() {
        fail(
            "The portable runtime is missing. Extract the complete ZIP into a new folder and try again.",
            None,
        );
        return ExitCode::FAILURE;
    }
    let mut command = Command::new(&python);
    command
        .arg("-I")
        .arg("-B")
        .arg("-u")
        .arg(root.join("portable_launcher.py"))
        .args(std::env::args_os().skip(1))
        .current_dir(&root)
        .creation_flags(CREATE_NO_WINDOW);
    match std::fs::File::create(&log) {
        Ok(file) => {
            let copy = file.try_clone();
            command.stdout(Stdio::from(file));
            match copy {
                Ok(other) => {
                    command.stderr(Stdio::from(other));
                }
                Err(_) => {
                    command.stderr(Stdio::null());
                }
            }
        }
        Err(_) => {
            command.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }
    match command.status() {
        Ok(status) if status.success() => ExitCode::SUCCESS,
        Ok(status) => {
            let code = status.code();
            // Deliberate terminations (Task Manager "End task" reports
            // 0xFFFFFFFF; console close reports 0xC000013A) are not errors.
            if matches!(code, Some(-1) | Some(-1073741510)) {
                return ExitCode::SUCCESS;
            }
            let tail = read_tail(&log);
            fail(
                &format!("RenderLock closed unexpectedly (exit code {:?}).\n\n{tail}", code),
                Some(&log),
            );
            ExitCode::FAILURE
        }
        Err(error) => {
            fail(&format!("RenderLock could not start the runtime: {error}"), Some(&log));
            ExitCode::FAILURE
        }
    }
}
