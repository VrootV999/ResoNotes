use std::io::Write;
use std::process::{Command, Stdio};

/// Ask the user for a short name (note / session). Returns `None` when the
/// user leaves it blank or cancels — the caller then falls back to the
/// current date-time. `RESONOTE_NAME` overrides the prompt (scripts/tests).
pub fn prompt_name(title: &str, text: &str) -> Option<String> {
    if let Ok(v) = std::env::var("RESONOTE_NAME") {
        let v = v.trim().to_string();
        return if v.is_empty() { None } else { Some(v) };
    }

    let ctl = crate::config::Config::load();
    let mode = ctl.name_prompt.clone().unwrap_or_else(|| "auto".into());
    if mode == "none" {
        return None;
    }
    let tools: &[&[&str]] = match mode.as_str() {
        "zenity" => &[&["zenity", "--entry"]],
        "kdialog" => &[&["kdialog", "--inputbox"]],
        "rofi" => &[&["rofi", "-dmenu"]],
        _ => &[
            &["rofi", "-dmenu"],
            &["zenity", "--entry"],
            &["kdialog", "--inputbox"],
        ],
    };
    for tool in tools {
        if let Some(name) = run_entry(tool, title, text) {
            return Some(name);
        }
    }
    None
}

/// Let the user pick one of `choices`; returns the index (or `None` on
/// cancel). `RESONOTE_PICK="<exact option text>"` overrides it (scripts/tests).
pub fn picker(title: &str, text: &str, choices: &[String]) -> Option<usize> {
    if let Ok(v) = std::env::var("RESONOTE_PICK") {
        let v = v.trim();
        return choices.iter().position(|c| c == v);
    }

    let ctl = crate::config::Config::load();
    let mode = ctl.name_prompt.clone().unwrap_or_else(|| "auto".into());
    if mode == "none" {
        return None;
    }
    let tools: &[&[&str]] = match mode.as_str() {
        "zenity" => &[&["zenity", "--list"]],
        "kdialog" => &[&["kdialog", "--menu"]],
        "rofi" => &[&["rofi", "-dmenu"]],
        _ => &[
            &["rofi", "-dmenu"],
            &["zenity", "--list"],
            &["kdialog", "--menu"],
        ],
    };
    for tool in tools {
        if let Some(picked) = run_picker(tool, title, text, choices) {
            return Some(picked);
        }
    }
    None
}

fn run_entry(tool: &[&str], title: &str, text: &str) -> Option<String> {
    let mut cmd = Command::new(tool[0]);
    let args: &[&str] = match tool[0] {
        "zenity" => &["--entry", "--title", title, "--text", text],
        "kdialog" => &["--title", title, "--inputbox", text],
        "rofi" => &["-dmenu", "-p", text, "-mesg", title],
        _ => return None,
    };
    let out = cmd
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .output()
        .ok()?;
    if !out.status.success() {
        return None; // cancelled
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

fn run_picker(tool: &[&str], title: &str, text: &str, choices: &[String]) -> Option<usize> {
    let mut cmd = Command::new(tool[0]);
    match tool[0] {
        "zenity" => {
            cmd.args(["--list", "--title", title, "--text", text, "--column", ""]);
            for c in choices {
                cmd.arg(c);
            }
        }
        "kdialog" => {
            cmd.args(["--title", title, "--menu", text]);
            for (i, c) in choices.iter().enumerate() {
                cmd.arg(i.to_string()).arg(c);
            }
        }
        "rofi" => {
            cmd.args(["-dmenu", "-p", text, "-mesg", title]);
            let mut child = cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().ok()?;
            if let Some(stdin) = child.stdin.as_mut() {
                for c in choices {
                    let _ = writeln!(stdin, "{c}");
                }
            }
            let out = child.wait_with_output().ok()?;
            if !out.status.success() {
                return None;
            }
            let picked = String::from_utf8_lossy(&out.stdout).trim().to_string();
            return choices.iter().position(|c| *c == picked);
        }
        _ => return None,
    }
    let out = cmd.stdin(Stdio::null()).stdout(Stdio::piped()).output().ok()?;
    if !out.status.success() {
        return None; // cancelled
    }
    let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if s.is_empty() {
        return None;
    }
    // kdialog prints the index; zenity prints the chosen label.
    choices.iter().position(|c| *c == s).or_else(|| s.parse::<usize>().ok())
}