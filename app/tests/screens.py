#!/usr/bin/env python3
"""The steps of app/tests/screens.sh: the app is driven through its web view's WebDriver (WebKitWebDriver),
every button found by its text or its label in the page, never by a place on the screen. A step whose text
does not appear fails the run.

    screens.py current   the screens in English against the stand-in's exits, the pictures 1-… to 6-…
    screens.py older     in Russian against exits as an older server sends them: they are under «Другие»
"""
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
# The visible and enabled one
FIND_BUTTON = BUTTONS_NAMED + "return buttons.find(button => !button.disabled && button.offsetParent !== null) ?? null;"
# Whether it is pressed or checked
IS_ON = BUTTONS_NAMED + 'return buttons.some(button => [button.ariaPressed, button.ariaChecked].includes("true"));'
FIND_INPUT = 'return document.querySelector(`input[aria-label="${arguments[0]}"]`);'
PAGE_TEXT = "return document.body.innerText;"


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


def shot(name: str):
    subprocess.run(["import", "-window", "root", f"{PICTURES}/{name}.png"], check=True)


def current(app: App):
    """The screens against the exits of today's server; the stand-in is asked for 5, Авто, then 2."""
    app.expect("Location", "Auto")
    shot("1-home")
    app.press("Connect")
    app.expect("Connected")
    shot("2-connected")
    app.press("Location")
    app.expect("Countries", "Netherlands", "Germany", "Finland", "United States", "Other")
    shot("3-location")
    app.press("Netherlands")
    app.expect("Best server", "Keep this IP", "The server may change", "198.51.100.52", "198.51.100.41")
    shot("3a-country")
    # the best of nl is 5: as few online as 2, fewer people
    app.press("Best server")
    app.expect("198.51.100.52", "Switch off and a server is chosen automatically, in any country")
    app.expect_on("Best server")
    shot("3b-best")
    # letting the address go is Авто anywhere: the list again, Авто chosen
    app.press("Keep this IP")
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
    shot("4-pinned")
    app.press("Location")
    app.expect("Recent", "198.51.100.41", "198.51.100.52")
    shot("4c-recent")
    app.fill("Search", "198.51")
    app.expect("198.51.100.38")
    app.expect_none("203.0.113", "Countries")
    shot("4d-search")
    # a server with an address is not found by its name, which the person never sees
    app.fill("Search", "exit8")
    app.expect("Nothing found")
    app.expect_none("203.0.113.40")
    app.expect_none("198.51.100")
    app.press("Back")
    app.press("All traffic")
    shot("4a-route")
    app.press("Pro++")
    shot("4b-protection")
    app.press("Network")
    shot("5-network")
    app.press("Settings")
    shot("6-settings")


def older(app: App):
    """In Russian, against an older server: every exit under «Другие», by its name; the stand-in is asked for
    1 (no one online anywhere: the largest share), then Авто."""
    app.expect("Локация", "Авто")
    app.press("Локация")
    app.expect("Авто", "Страны", "Другие")
    app.expect_none("Нидерланды", "Германия")
    shot("7-older")
    app.press("Другие")
    app.expect("Лучший сервер", "Зафиксировать IP", "Сервер может смениться", "Наименее загруженные")
    app.expect("exit1-nl", "exit2-nl", "exit4-de", "exit8")
    app.expect_none("exit3-fi")
    shot("7a-older-other")
    app.fill("Поиск по IP", "exit4")
    app.expect("exit4-de")
    app.expect_none("exit1-nl")
    app.fill("Поиск по IP", "")
    app.press("Лучший сервер")
    app.expect_on("Лучший сервер")
    app.expect("Выключите — сервер выберется автоматически, в любой стране")
    shot("7b-older-best")
    app.press("Назад")
    app.expect("Недавние", "exit1-nl", "Другие · зафиксирован")
    app.press("Другие")
    app.press("Зафиксировать IP")
    app.expect("Страны")
    app.expect_on("Авто")
    shot("7c-older-auto")


STEPS = {"current": current, "older": older}

if __name__ == "__main__":
    app = App()
    try:
        STEPS[sys.argv[1]](app)
    except Failed as failure:
        print(f"app screens ({sys.argv[1]}): {failure}", file=sys.stderr)
        sys.exit(1)
    finally:
        app.close()
