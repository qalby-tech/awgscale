//! Only in a build for the pictures of the screens (feature `pictures`, never packaged): the
//! buttons named in AWGSCALE_PRESS (their texts or labels, separated by "|", found the way
//! app/tests/screens.py finds them) are pressed one after another, each once it shows. On a computer
//! screens.py presses through the web view's WebDriver; the iOS simulator has none, so its pictures
//! (.github/workflows/app-ios.yml) start the app with this variable instead.

use std::time::Duration;

use tauri::{AppHandle, Manager};

const VARIABLE: &str = "AWGSCALE_PRESS";
/// Before the first press the window loads; between presses the screen changes.
const PAUSE: Duration = Duration::from_secs(3);

/// Finds the visible, enabled button named so (by its label, or by its title, or by its text when it has
/// no title, as screens.py) and presses it, trying again for 15 seconds.
const PRESS: &str = r#"
(wanted => {
  const started = Date.now();
  const named = button => {
    const title = button.querySelector(".strong");
    return title == null ? button.innerText.trim() : title.innerText.trim();
  };
  const press = () => {
    const button = [...document.querySelectorAll("button")].find(button =>
      !button.disabled && button.offsetParent !== null &&
      (button.getAttribute("aria-label") === wanted || named(button) === wanted));
    if (button != null) {
      button.click();
    } else if (Date.now() - started < 15000) {
      setTimeout(press, 250);
    }
  };
  press();
})"#;

pub fn press_from_environment(app: &AppHandle) {
    let Ok(wanted) = std::env::var(VARIABLE) else {
        return;
    };
    let app = app.clone();
    std::thread::spawn(move || {
        for text in wanted.split('|').filter(|text| !text.is_empty()) {
            std::thread::sleep(PAUSE);
            let Some(window) = app.get_webview_window("main") else {
                return;
            };
            let Ok(quoted) = serde_json::to_string(text) else {
                return;
            };
            let _ = window.eval(format!("{PRESS}({quoted});"));
        }
    });
}
