#!/usr/bin/env python3
"""The steps of app/tests/android.sh, run in the emulator's container: the emulator itself (headless), the
APK installed, the app driven through adb and uiautomator. Every button is found by its text in the
screen's tree (the web view hands its page to Android's accessibility), never by a place on the screen. A
step whose text does not appear fails the run.

    android.py <apk> <test-panel apk> <pictures> <client.conf>

The APK is the one people install: everything but the join runs on it. The join runs last, on the build that
trusts the test's stand-in panel (app/build-android.sh test-panel), installed over it.

What the tests ask the server (the app's requests through the tunnel) is checked by android.sh.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PACKAGE = "tech.qalby.awgscale"
DATA = f"/data/data/{PACKAGE}"
AVD = "phone"
WAIT_SECONDS = 30
# the tunnel is up while its last handshake is younger than this (handshakeFresh in app/qalby/mobile)
HANDSHAKE_FRESH_SECONDS = 180
BOOT_SECONDS = 300
# the screen of a phone: 1080 × 2340 at 440 dpi is 393 CSS pixels wide, as the screens were drawn (390)
SCREEN = {"hw.lcd.width": "1080", "hw.lcd.height": "2340", "hw.lcd.density": "440"}
# the network's own address inside the tunnel (the app API's stand-in answers there), and the device's
NETWORK_HOST = "172.16.17.1"
DEVICE_ADDRESS = "172.16.17.15"
# from the emulator, 10.0.2.2 is its container's own 127.0.0.1, where the stand-in for the pictures listens
HOST_LOOPBACK = "10.0.2.2"
# a made-up invitation of the shape the panel hands out (a 22-character secret)
LINK = "awgscale://join?h=vpn.example.org&c=Zx3kQ0aB1cD2eF3gH4iJ5k"

failures: list[str] = []


class Failed(Exception):
    pass


def adb(*arguments: str, check: bool = True, timeout: int = 60) -> str:
    done = subprocess.run(["adb", *arguments], capture_output=True, text=True, timeout=timeout)
    if check and done.returncode != 0:
        raise Failed(f"adb {' '.join(arguments)}: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def shell(command: str, check: bool = False) -> str:
    return adb("shell", command, check=check)


def check(name: str, passed: bool) -> None:
    print(("ok " if passed else "FAIL ") + name, flush=True)
    if not passed:
        failures.append(name)


def start_emulator() -> None:
    home = Path(os.environ["HOME"]) / ".android" / "avd"
    (home / f"{AVD}.avd").mkdir(parents=True, exist_ok=True)
    (home / f"{AVD}.ini").write_text(f"avd.ini.encoding=UTF-8\npath={home}/{AVD}.avd\ntarget=android-35\n")
    config = {
        "image.sysdir.1": "system-images/android-35/google_apis/x86_64/",
        "tag.id": "google_apis",
        "abi.type": "x86_64",
        "hw.cpu.arch": "x86_64",
        "hw.cpu.ncore": "4",
        "hw.ramSize": "3072",
        "hw.keyboard": "yes",
        "disk.dataPartition.size": "4G",
        "PlayStore.enabled": "false",
        **SCREEN,
    }
    (home / f"{AVD}.avd" / "config.ini").write_text("".join(f"{key}={value}\n" for key, value in config.items()))
    log = open("/tmp/emulator.log", "w")
    subprocess.Popen(
        ["emulator", "-avd", AVD, "-no-window", "-gpu", "swiftshader_indirect", "-no-audio", "-no-boot-anim",
         "-no-snapshot", "-no-metrics", "-wipe-data"],
        stdout=log, stderr=subprocess.STDOUT, cwd="/tmp")
    adb("wait-for-device", timeout=BOOT_SECONDS)
    deadline = time.monotonic() + BOOT_SECONDS
    while shell("getprop sys.boot_completed").strip() != "1":
        if time.monotonic() > deadline:
            raise Failed("the emulator did not boot: " + Path("/tmp/emulator.log").read_text()[-2000:])
        time.sleep(2)
    for setting in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        shell(f"settings put global {setting} 0")
    adb("root")
    adb("wait-for-device")
    time.sleep(2)


# The screen's tree: every node with a text, its words and where it is
NODE = re.compile(r"<node [^>]*>")


def nodes() -> list[dict]:
    for _ in range(5):
        dumped = shell("uiautomator dump /sdcard/screen.xml >/dev/null 2>&1 && cat /sdcard/screen.xml")
        if "<hierarchy" in dumped:
            break
        time.sleep(1)
    found = []
    for match in NODE.finditer(dumped):
        node = match.group(0)
        attribute = lambda name: (re.search(f' {name}="([^"]*)"', node) or [None, ""])[1]  # noqa: E731
        text = (attribute("text") or attribute("content-desc")).replace("&amp;", "&")
        bounds = [int(number) for number in re.findall(r"\d+", attribute("bounds"))]
        kind = attribute("class")
        if (text or kind == "android.widget.EditText") and len(bounds) == 4:
            found.append({"text": text, "clickable": attribute("clickable") == "true", "bounds": bounds, "class": kind})
    return found


def screen_text() -> str:
    return " | ".join(node["text"] for node in nodes())


def wait_for(text: str, clickable: bool = False, seconds: int = WAIT_SECONDS) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for node in nodes():
            if (node["text"] == text or text in node["text"].split(" ")) and (node["clickable"] or not clickable):
                return node
        time.sleep(0.5)
    raise Failed(f"«{text}» did not appear; the screen shows: {screen_text()}")


def press(text: str, seconds: int = WAIT_SECONDS) -> None:
    left, top, right, bottom = wait_for(text, clickable=True, seconds=seconds)["bounds"]
    shell(f"input tap {(left + right) // 2} {(top + bottom) // 2}")


def picture(pictures: Path, name: str) -> None:
    (pictures / f"{name}.png").write_bytes(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True).stdout)


def launch() -> None:
    shell(f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1")


def stop_app() -> None:
    shell(f"am force-stop {PACKAGE}")


def seed(profile: dict) -> None:
    """The network's profile, put where the app keeps it (app_data_dir/network.json), as a joined device has it."""
    stop_app()
    Path("/tmp/network.json").write_text(json.dumps(profile))
    adb("push", "/tmp/network.json", "/data/local/tmp/network.json")
    os.remove("/tmp/network.json")
    owner = shell(f"stat -c %u:%g {DATA}").strip()
    shell(f"cp /data/local/tmp/network.json {DATA}/network.json && rm /data/local/tmp/network.json && "
          f"chown {owner} {DATA}/network.json && chmod 600 {DATA}/network.json && restorecon {DATA}/network.json", check=True)


def reaches(address: str) -> bool:
    return " 0% packet loss" in shell(f"ping -c 2 -W 2 {address}")


def steps(apk: str, test_panel_apk: str, pictures: Path, conf: str) -> None:
    start_emulator()
    adb("install", "-r", apk, timeout=180)
    check("the APK installs", PACKAGE in shell("pm list packages"))
    # the notification that says the VPN is on: granted here, Android 13+ asks the person
    shell(f"pm grant {PACKAGE} android.permission.POST_NOTIFICATIONS")

    launch()
    wait_for("Connect to your network")
    picture(pictures, "1-start")
    check("the start screen fits the phone's width", all(node["bounds"][2] <= 1080 for node in nodes()))

    # the join link opens the app on it (the intent filter), and fills the field
    shell(f"am start -a android.intent.action.VIEW -d '{LINK}'")
    wait_for(LINK)
    picture(pictures, "2-opened-link")
    check("an awgscale:// link opens the app with it in the field", True)

    # typed into the field as pasted text arrives (the system's paste puts the same text there)
    stop_app()
    launch()
    wait_for("Invitation link")
    field = next(node for node in nodes() if node["class"] == "android.widget.EditText")["bounds"]
    shell(f"input tap {(field[0] + field[2]) // 2} {(field[1] + field[3]) // 2}")
    shell("input text " + LINK.replace("&", "\\&").replace("?", "\\?"))
    wait_for(LINK)
    # through the system's clipboard: all of it cut, then pasted back
    shell("input keycombination KEYCODE_CTRL_LEFT KEYCODE_A")
    shell("input keyevent KEYCODE_CUT")
    time.sleep(1)
    check("the link cut from the field leaves it empty", LINK not in screen_text())
    shell("input keyevent KEYCODE_PASTE")
    wait_for(LINK)
    check("the link pasted into the field", True)
    press("Continue")
    wait_for("No connection to the network's server — check the internet")
    check("a link in the field is looked at (the panel is out of reach here: the app says so)", True)

    # the screens against the stand-in of the app API, outside any tunnel
    seed({"host": "vpn.example.org", "name": "test_phone", "address": "172.16.17.23", "entry": HOST_LOOPBACK,
          "owner": True, "conf": "[Interface]\nPrivateKey = made-up\n"})
    launch()
    wait_for("Disconnected")
    wait_for("owner")  # /app/api/me answered
    picture(pictures, "3-home")
    press("Location")
    wait_for("Auto")
    wait_for("Netherlands")
    picture(pictures, "4-location")
    shell("input keyevent KEYCODE_BACK")

    # the tunnel, end to end: the network's address is reached only through it
    seed({"host": "vpn.example.org", "name": "test_phone", "address": DEVICE_ADDRESS, "entry": NETWORK_HOST,
          "owner": True, "conf": conf})
    check("without the tunnel the network's address is out of reach", not reaches(NETWORK_HOST))
    launch()
    press("Connect")
    press("OK")  # Android asks once whether the app may be the VPN
    wait_for("Connected", seconds=60)
    time.sleep(2)  # the network's answers (me, exits) fill the screen; the accessibility tree lags behind it
    picture(pictures, "5-connected")
    check("the tunnel's interface has the device's address", f"inet {DEVICE_ADDRESS}/32" in shell("ip addr show tun0"))
    check("the network's address answers through the tunnel", reaches(NETWORK_HOST))
    check("the notification says the VPN is on", f"pkg={PACKAGE}" in shell("dumpsys notification"))
    routes = shell("dumpsys connectivity | grep -m1 'InterfaceName: tun0'")
    check("all of IPv4 but the local networks goes into the tunnel", "11.0.0.0/8" in routes and "10.0.0.0/8" not in routes)
    check("the network's own subnet stays in the tunnel", "172.16.17.0/24" in routes)
    check("the network's DNS is the VPN's", f"DnsAddresses: [ /{NETWORK_HOST} ]" in routes)

    # the window goes (and with it the app's process): the tunnel stays
    shell("input keyevent KEYCODE_BACK")
    time.sleep(3)
    check("the app's process is gone with its window", shell(f"pidof {PACKAGE}").strip() == "")
    check("the tunnel's process stays", shell(f"pidof {PACKAGE}:tunnel").strip() != "")
    check("the tunnel still carries", reaches(NETWORK_HOST))
    launch()
    wait_for("Connected")
    check("the app opened again finds the tunnel up", True)

    # the server stops answering: up only while the last handshake is younger than 3 minutes
    endpoint = re.search(r"^Endpoint\s*=\s*(?P<host>[^:\s]+):(?P<port>\d+)", conf, re.MULTILINE)
    rule = f"OUTPUT -p udp -d {endpoint['host']} --dport {endpoint['port']} -j DROP"
    shell(f"iptables -I {rule}", check=True)
    wait_for("Connecting…", seconds=HANDSHAKE_FRESH_SECONDS + 30)
    picture(pictures, "6-server-gone")
    check("the server silent for 3 minutes: no longer up", True)
    shell(f"iptables -D {rule}", check=True)
    reaches(NETWORK_HOST)  # traffic into the tunnel makes it shake hands again (no traffic: no handshake)
    wait_for("Connected", seconds=90)
    check("the server back: up again", True)

    press("Disconnect")
    wait_for("Disconnected")
    time.sleep(1)
    check("down: no interface", "tun0" not in shell("ip link show"))
    check("down: the network's address is out of reach again", not reaches(NETWORK_HOST))
    check("down: no notification", f"pkg={PACKAGE}" not in shell("dumpsys notification"))
    picture(pictures, "7-disconnected")

    # a device joins with the link, the way a person does it, on the build that trusts the stand-in panel
    adb("install", "-r", test_panel_apk, timeout=180)
    shell(f"pm clear {PACKAGE}")
    shell(f"pm grant {PACKAGE} android.permission.POST_NOTIFICATIONS")
    shell(f"am start -a android.intent.action.VIEW -d '{LINK}'")
    wait_for("Name this device")
    picture(pictures, "8-invitation")
    field = next(node for node in nodes() if node["class"] == "android.widget.EditText")["bounds"]
    shell(f"input tap {(field[0] + field[2]) // 2} {(field[1] + field[3]) // 2}")
    shell("input text test_join")
    press("Connect this device")
    wait_for("Disconnected", seconds=60)
    check("the joined network is kept by the app", "test_join" in shell(f"cat {DATA}/network.json"))
    press("Connect")
    try:
        press("OK", seconds=5)  # Android asks again when it forgot the app's consent with its data
    except Failed:
        pass
    wait_for("Connected", seconds=60)
    check("the joined device's tunnel is up", reaches(NETWORK_HOST))
    picture(pictures, "9-joined-connected")
    press("Disconnect")
    wait_for("Disconnected")

    # the always-on VPN: Android starts the service itself (at boot: the person chose the app in the VPN settings),
    # nothing of the app running; it comes up with the network the app keeps. Started here the way Android
    # starts it, since the emulator's network does not come back after a reboot of its guest
    stop_app()
    check("nothing of the app runs", shell(f"pidof {PACKAGE}:tunnel").strip() == "" and shell(f"pidof {PACKAGE}").strip() == "")
    shell(f"am start-foreground-service -a android.net.VpnService -n {PACKAGE}/.tunnel.AwgVpnService", check=True)
    deadline = time.monotonic() + 60
    while not reaches(NETWORK_HOST) and time.monotonic() < deadline:
        time.sleep(2)
    check("always-on: the service started by Android brings the kept network up, the app not opened", reaches(NETWORK_HOST))
    check("always-on: the app's window was not opened for it", shell(f"pidof {PACKAGE}").strip() == "")
    shell(f"am start-foreground-service -a tech.qalby.awgscale.tunnel.DOWN -n {PACKAGE}/.tunnel.AwgVpnService")


def main() -> None:
    apk, test_panel_apk, pictures, conf_path = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4]
    try:
        steps(apk, test_panel_apk, pictures, Path(conf_path).read_text())
    except Failed as failure:
        print(f"FAIL {failure}", flush=True)
        failures.append(str(failure))
        picture(pictures, "failed")
    if failures:
        Path(pictures / "logcat.txt").write_text(adb("logcat", "-d", check=False)[-400000:])
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
