#!/usr/bin/env python3
"""The steps of app/tests/screens.sh: the app is driven through its web view's WebDriver (WebKitWebDriver),
every button found by its text or its label in the page, never by a place on the screen. A step whose text
does not appear fails the run.

    screens.py current   the screens in English against the stand-in's exits, the pictures 1-… to 6-…
    screens.py older     in Russian against exits as an older server sends them: they are under «Другие»
    screens.py manual    the first run of a computer (no camera: no «Scan QR code»), a connection given by
                         hand (an AmneziaWG config, made by the panel's generator with throwaway keys) taken
                         as the device's network: a tunnel and no panel, then removed
"""
from pathlib import Path
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

DRIVER = "http://127.0.0.1:4444"
APP = "/app/target/pretend/release/awgscale-app"
PICTURES = "/app/target/screens"
# How WebDriver names an element it hands over
ELEMENT = "element-6066-11e4-a52e-4f735466cecf"
WAIT_SECONDS = 15
STEP_SECONDS = 0.6

# The buttons named so: by their label, by their title (the strong part of a row: a server's address, a
# country's name, not what is said of it), or by their text when they have no title
BUTTONS_NAMED = """
const wanted = arguments[0];
const named = button => {
  const title = button.querySelector(".strong");
  return title == null ? button.innerText.trim() : title.innerText.trim();
};
const buttons = [...document.querySelectorAll("button")].filter(button =>
  button.getAttribute("aria-label") === wanted || named(button) === wanted);
"""
# The flags drawn where the selector says: how many are pictures that loaded, and how many are globes
FLAGS = """
const flags = [...document.querySelectorAll(arguments[0])].filter(flag => flag.offsetParent !== null);
return {
  pictures: flags.filter(flag => flag.querySelector("img")?.naturalWidth > 0).length,
  globes: flags.filter(flag => flag.querySelector("img") == null && flag.querySelector("svg") != null).length,
};
"""
# The tab pressed, and the connection's screen looked at before anything could be asked again: the network's
# answers are already there (the location row enabled, the protection's chips shown)
TAB_AT_ONCE = """
const done = arguments[2];
const tab = [...document.querySelectorAll("nav button")].find(button => button.innerText.trim() === arguments[0]);
tab.click();
setTimeout(() => {
  const place = [...document.querySelectorAll("button.place")].find(button => button.offsetParent !== null);
  const chips = [...document.querySelectorAll(`.chips[aria-label="${arguments[1]}"] .chip`)].filter(chip => chip.offsetParent !== null);
  done({ place: place != null && !place.disabled, chips: chips.length });
}, 30);
"""
# The protection's chips named pressed one after another in the same moment, and the chips looked at 50 ms
# later: which one is marked, and whether the network is still applying it
CHIPS_AT_ONCE = """
const done = arguments[2];
const group = document.querySelector(`.chips[aria-label="${arguments[0]}"]`);
const chip = text => [...group.querySelectorAll(".chip")].find(chip => chip.innerText.trim() === text);
for (const text of arguments[1]) {
  chip(text).click();
}
setTimeout(() => done({
  pressed: [...group.querySelectorAll('.chip[aria-pressed="true"]')].map(chip => chip.innerText.trim()),
  busy: group.getAttribute("aria-busy") === "true",
}), 50);
"""
# Whether the protection's chips wait for the network
CHIPS_BUSY = 'return document.querySelector(`.chips[aria-label="${arguments[0]}"]`)?.getAttribute("aria-busy") === "true";'
# How many of the protection's chips are shown
CHIPS_SHOWN = 'return document.querySelectorAll(`.chips[aria-label="${arguments[0]}"] .chip`).length;'
# The visible and enabled one
FIND_BUTTON = BUTTONS_NAMED + "return buttons.find(button => !button.disabled && button.offsetParent !== null) ?? null;"
# Whether it is pressed or checked
IS_ON = BUTTONS_NAMED + 'return buttons.some(button => [button.ariaPressed, button.ariaChecked].includes("true"));'
# A field by its label: its own (aria-label) or the text of the label around it
FIND_INPUT = """
const wanted = arguments[0];
const labelled = [...document.querySelectorAll("label")].find(label => label.querySelector("span")?.innerText.trim() === wanted);
return document.querySelector(`input[aria-label="${wanted}"], textarea[aria-label="${wanted}"]`) ??
  labelled?.querySelector("input, textarea") ?? null;
"""
PAGE_TEXT = "return document.body.innerText;"
# The join screen's one field, by its own label (the screen shows no text around it): its label and placeholder
JOIN_FIELD = """
const field = document.querySelector("main.start textarea");
return field == null ? null : {label: field.getAttribute("aria-label"), placeholder: field.placeholder};
"""
# How long a connection given by hand is given to find an app API behind its tunnel (5 s at most, in the core)
PROBE_SECONDS = 6
# WebDriver's Enter key
ENTER = "\ue007"
# The app API's stand-in (tests/standin.py), told how to answer the protection's choice
STANDIN = "http://127.0.0.1:8088"


class Failed(Exception):
    pass


def call(method: str, path: str, body: dict | None = None) -> object:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(DRIVER + path, method=method, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as answer:
            return json.load(answer)["value"]
    except urllib.error.HTTPError as failure:
        raise Failed(f"{method} {path}: {failure.read().decode()[:300]}") from None


class App:
    def __init__(self):
        capabilities = {"webkitgtk:browserOptions": {"binary": APP, "args": []}}
        session = call("POST", "/session", {"capabilities": {"alwaysMatch": capabilities}})
        self.__session = f"/session/{session['sessionId']}"

    def close(self):
        call("DELETE", self.__session)

    def __script(self, script: str, *arguments: str) -> object:
        return call("POST", f"{self.__session}/execute/sync", {"script": script, "args": list(arguments)})

    def __until(self, what: str, check):
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            found = check()
            if found:
                return found
            time.sleep(0.25)
        text = self.__script(PAGE_TEXT)
        raise Failed(f"{what} did not appear; the page shows: {text!r}")

    def flags(self, selector: str) -> dict:
        return self.__script(FLAGS, selector)

    def join_field(self) -> dict | None:
        return self.__script(JOIN_FIELD)

    def placeholder(self, label: str) -> str:
        return self.__script(f'return document.querySelector(`input[aria-label="{label}"]`)?.placeholder ?? "";')

    # a tab pressed and the screen read in the same moment, in the page itself
    def tab_at_once(self, tab: str, chips: str) -> dict:
        return call("POST", f"{self.__session}/execute/async", {"script": TAB_AT_ONCE, "args": [tab, chips]})

    # chips pressed in the same moment, and looked at 50 ms later
    def chips_at_once(self, group: str, *chips: str) -> dict:
        return call("POST", f"{self.__session}/execute/async", {"script": CHIPS_AT_ONCE, "args": [group, list(chips)]})

    def chips_shown(self, group: str) -> int:
        return self.__script(CHIPS_SHOWN, group)

    def until_applied(self, group: str):
        self.__until(f"the chips «{group}» applied", lambda: not self.__script(CHIPS_BUSY, group))

    # as shown, without the case the styles give it (a section's heading is in capitals)
    def text(self) -> str:
        return self.__script(PAGE_TEXT).casefold()

    def expect(self, *texts: str):
        for wanted in texts:
            self.__until(f"«{wanted}»", lambda: wanted.casefold() in self.text())

    def expect_none(self, *texts: str):
        shown = self.text()
        for unwanted in texts:
            if unwanted.casefold() in shown:
                raise Failed(f"«{unwanted}» is shown and should not be; the page shows: {shown!r}")

    def expect_on(self, text: str):
        self.__until(f"«{text}» chosen", lambda: self.__script(IS_ON, text))

    def press(self, text: str):
        button = self.__until(f"the button «{text}»", lambda: self.__script(FIND_BUTTON, text))
        call("POST", f"{self.__session}/element/{button[ELEMENT]}/click", {})
        time.sleep(STEP_SECONDS)

    def fill(self, label: str, text: str):
        field = self.__until(f"the field «{label}»", lambda: self.__script(FIND_INPUT, label))
        call("POST", f"{self.__session}/element/{field[ELEMENT]}/clear", {})
        if text:
            call("POST", f"{self.__session}/element/{field[ELEMENT]}/value", {"text": text})
        time.sleep(STEP_SECONDS)


def standin_filter(**how):
    """How the stand-in answers the protection's choice from now on: delay (seconds), refuse, locked."""
    request = urllib.request.Request(f"{STANDIN}/test/filter", method="POST", data=json.dumps(how).encode(),
                                     headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=5).close()


def shot(name: str):
    subprocess.run(["import", "-window", "root", f"{PICTURES}/{name}.png"], check=True)


def check(what: str, true: bool):
    if not true:
        raise Failed(what)


def current(app: App):
    """The screens against the exits of today's server; the stand-in is asked for 5, Авто, then 2."""
    app.expect("Location", "Auto", "Protection")
    # no «as my group»: the level that applies is the one chosen, Standard for a group of Standard
    app.expect_on("Standard")
    app.expect_none("As my group")
    shot("1-home")
    app.press("About the protection levels")
    app.expect("Protection levels", "Off — nothing is blocked", "Standard — blocks ads and dangerous sites",
               "Pro — also trackers", "Pro++ — stricter", "Ultimate — the strictest", "Kids — also adult sites")
    shot("1a-protection-levels")
    app.press("Close")
    app.press("Connect")
    app.expect("Connected")
    shot("2-connected")
    app.press("Location")
    app.expect("Countries", "Netherlands", "Germany", "Finland", "United States", "Other")
    flags = app.flags(".option .code.flag")
    check(f"the countries' flags are drawn, a globe for «Other»: {flags}", flags == {"pictures": 4, "globes": 1})
    check("the search asks for a country", app.placeholder("Search") == "Country")
    shot("3-location")
    app.press("Netherlands")
    app.expect("Best server", "Fixed IP", "A server is picked automatically; your IP may change", "198.51.100.52",
               "198.51.100.41")
    app.expect_none("Search by IP", "Keep this IP")
    check("the country's flag in its header", app.flags(".head .code.flag")["pictures"] == 1)
    shot("3a-country")
    # the best of nl is 5: as few online as 2, fewer people
    app.press("Best server")
    app.expect("Always this server: sites see the same IP 198.51.100.52")
    app.expect_on("Best server")
    app.expect_on("Fixed IP")
    shot("3b-best")
    # letting the address go is Авто anywhere: the list again, Авто chosen
    app.press("Fixed IP")
    app.expect("Countries")
    app.expect_on("Auto")
    shot("3c-unpinned")
    app.press("Netherlands")
    app.press("198.51.100.41")
    app.expect_on("198.51.100.41")
    shot("3d-server")
    app.press("Back")
    app.press("Back")
    app.expect("198.51.100.41 · pinned")
    check("the location's row shows the country's flag", app.flags(".place .code.flag")["pictures"] == 1)
    shot("4-pinned")
    app.press("Location")
    app.expect("Recent", "198.51.100.41", "198.51.100.52")
    shot("4c-recent")
    # a search is by country only: by its name, never by a server's address or name
    app.fill("Search", "nether")
    app.expect("Netherlands")
    app.expect_none("Germany", "198.51.100", "Countries")
    check("the found country has its flag", app.flags(".option .code.flag")["pictures"] == 1)
    shot("4d-search")
    app.fill("Search", "198.51")
    app.expect("Nothing found")
    app.expect_none("198.51.100")
    app.fill("Search", "exit8")
    app.expect("Nothing found")
    app.expect_none("203.0.113.40")
    app.fill("Search", "")
    app.press("Back")
    app.press("All traffic")
    shot("4a-route")
    app.press("Pro++")
    app.expect_on("Pro++")
    shot("4b-protection")
    app.press("Network")
    shot("5-network")
    app.press("Settings")
    app.expect("Kill switch", "Block the internet if the VPN drops", "Split tunneling",
               "Local networks", "Sign out")
    app.expect_none("No internet without VPN", "Bypass VPN", "Leave the network")
    shot("6-settings")
    app.press("Sign out")
    app.expect("Sign out?", "This device forgets the network; you will need a new invitation to connect again.")
    shot("6a-sign-out")
    app.press("Cancel")
    # back on the connection's tab: whole at once, from what was known, not after the network answers again
    whole = app.tab_at_once("Connection", "Protection")
    check(f"the connection's tab shows all of itself at once: {whole}", whole["place"] and whole["chips"] == 6)
    app.expect_on("Pro++")
    protection(app)


def protection(app: App):
    """The chosen level is marked at once while the network takes its time, goes back when the network refuses
    it, and the last of quick presses wins; Off is one of the levels; a children's group offers none."""
    standin_filter(delay=2)
    at_once = app.chips_at_once("Protection", "Off")
    check(f"Off is marked at once, the network still applying it: {at_once}",
          at_once == {"pressed": ["Off"], "busy": True})
    shot("4e-protection-applying")
    app.until_applied("Protection")
    app.expect_on("Off")
    app.expect_none("Something went wrong")
    # refused: the level marked at once goes back to the one the network holds, and what it said is shown
    standin_filter(delay=1.5, refuse=True)
    at_once = app.chips_at_once("Protection", "Ultimate")
    check(f"Ultimate is marked at once: {at_once}", at_once == {"pressed": ["Ultimate"], "busy": True})
    app.expect("Protection not applied: the blocker does not answer")
    app.until_applied("Protection")
    app.expect_on("Off")
    shot("4f-protection-refused")
    # three presses at once: the last one is marked at once and is the one the network ends on
    standin_filter(delay=1, refuse=False)
    at_once = app.chips_at_once("Protection", "Pro", "Ultimate", "Kids")
    check(f"the last of quick presses is marked at once: {at_once}", at_once == {"pressed": ["Kids"], "busy": True})
    app.until_applied("Protection")
    app.expect_on("Kids")
    app.expect_none("Protection not applied")
    # an adult set the level (a children's group): it is shown, no level is offered, Off neither
    standin_filter(delay=0, locked=True)
    app.press("Network")
    app.press("Connection")
    app.expect("Set by an adult")
    check("a children's group offers no level", app.chips_shown("Protection") == 0)
    shot("4g-protection-locked")


def older(app: App):
    """In Russian, against an older server: every exit under «Другие», by its name; the stand-in is asked for
    1 (no one online anywhere: the largest share), then Авто."""
    app.expect("Локация", "Авто", "Защита")
    app.expect_on("Обычная")
    app.expect_none("Как у группы")
    app.press("О режимах защиты")
    app.expect("Режимы защиты", "Выключена — ничего не блокируется", "Обычная — реклама и опасные сайты",
               "Детская — ещё сайты для взрослых")
    shot("7-older-levels")
    app.press("Закрыть")
    app.press("Локация")
    app.expect("Авто", "Страны", "Другие")
    app.expect_none("Нидерланды", "Германия")
    flags = app.flags(".option .code.flag")
    # «Другие» and the servers chosen lately in the English run (by their index: here of no known country)
    check(f"exits of no known country: globes, no flag: {flags}", flags["pictures"] == 0 and flags["globes"] >= 1)
    check("the search asks for a country", app.placeholder("Поиск") == "Страна")
    shot("7-older")
    app.press("Другие")
    app.expect("Лучший сервер", "Постоянный IP", "Сервер выбирается автоматически, IP может меняться",
               "Наименее загруженные")
    app.expect("exit1-nl", "exit2-nl", "exit4-de", "exit8")
    app.expect_none("exit3-fi", "Поиск по IP")
    shot("7a-older-other")
    app.press("Лучший сервер")
    app.expect_on("Лучший сервер")
    # an older server sends no address: the IP is not named
    app.expect("Всегда этот сервер: сайты видят один и тот же IP")
    shot("7b-older-best")
    app.press("Назад")
    app.expect("Недавние", "exit1-nl", "Другие · зафиксирован")
    app.press("Другие")
    app.press("Постоянный IP")
    app.expect("Страны")
    app.expect_on("Авто")
    shot("7c-older-auto")
    app.press("Назад")
    app.press("Настройки")
    app.expect("Kill switch", "Блокировать интернет, если VPN отключился", "Раздельное туннелирование",
               "Локальные сети", "Выйти")
    app.expect_none("Без VPN нет интернета", "Локальные сети мимо VPN", "Выйти из сети")
    shot("7d-older-settings")
    app.press("Выйти")
    app.expect("Выйти?", "Устройство забудет сеть: чтобы подключиться снова, понадобится новое приглашение.")
    app.press("Отмена")


def manual(app: App):
    """The join screen of a computer, then connections given by hand: a foreign server's (no app API behind its
    tunnel) is a tunnel only, no location; one whose server answers as an awgscale network (the stand-in, at the
    config's DNS) is shown as that network."""
    app.expect("Connect to your network", "Continue", "Open a file")
    # the field alone: no label, no hint, no placeholder around it; it is named for a screen reader only
    app.expect_none("Scan QR code", "Invitation link", "Or an AmneziaVPN key", "awgscale://join")
    field = app.join_field()
    check(f"the join field is named and has no placeholder: {field}",
          field == {"label": "Invitation link or key", "placeholder": ""})
    shot("8-start")
    app.fill("Invitation link or key", "WIFI:S:home;T:WPA;P:made-up;;")
    app.press("Continue")
    app.expect("This is neither an awgscale invitation nor an AmneziaWG key or config")
    # WebKit's WebDriver types a line's end as nothing: each one is pressed as Enter (U+E007), a new line here
    ours = Path("/app/core/tests/fixtures/our.conf").read_text()
    app.fill("Invitation link or key", ours.replace("\n", ENTER))
    app.press("Continue")
    app.expect("AmneziaWG connection", "vpn.example.org", "Disconnected")
    app.expect_none("Location")
    shot("8a-manual")
    app.press("Connect")
    app.expect("Connected", "172.16.17.15")
    # nothing answers at its DNS (172.16.17.1 is nowhere here): the server is not an awgscale network
    time.sleep(PROBE_SECONDS)
    app.expect("AmneziaWG connection")
    app.expect_none("Location", "Protection", "My network")
    shot("8b-manual-connected")
    app.press("Settings")
    app.expect("AmneziaWG connection", "Server", "vpn.example.org", "Remove the connection")
    app.expect_none("This device")
    shot("8c-manual-settings")
    app.press("Remove the connection")
    app.press("Remove")
    app.expect("Connect to your network")
    # the same config, its DNS where the app API's stand-in answers: an awgscale network behind a key
    app.fill("Invitation link or key", ours.replace("DNS = 172.16.17.1", "DNS = 127.0.0.1").replace("\n", ENTER))
    app.press("Continue")
    app.expect("AmneziaWG connection", "Disconnected")
    app.press("Connect")
    app.expect("Connected", "My network", "Location", "Protection")
    app.expect_none("AmneziaWG connection")
    shot("8d-manual-network")
    app.press("Settings")
    app.expect("Network", "vpn.example.org", "This device", "test_laptop", "Remove the connection")
    app.expect_none("Sign out")
    shot("8e-manual-network-settings")
    app.press("Remove the connection")
    app.press("Remove")
    app.expect("Connect to your network")


STEPS = {"current": current, "older": older, "manual": manual}

if __name__ == "__main__":
    app = App()
    try:
        STEPS[sys.argv[1]](app)
    except Failed as failure:
        print(f"app screens ({sys.argv[1]}): {failure}", file=sys.stderr)
        sys.exit(1)
    finally:
        app.close()
