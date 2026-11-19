"""What a device is given: its config, the vpn:// string of the AmneziaVPN app, and both as QR pictures."""
from __future__ import annotations

import base64
import io
import json
import zlib
from typing import Any

import segno

from .conf import InterfaceBlock
from .keys import public_of
from .models import DevicePayload, User
from .settings import UsersSettings

# the tunnel's obfuscation settings: the same lines go into every device's config
SHARED_FIELDS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4", "I1", "I2", "I3", "I4", "I5",
                 "HeaderProtectionKey", "ContentPaddingAddition")
URI_FIELDS = ("H1", "H2", "H3", "H4", "Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "I1", "I2", "I3", "I4", "I5",
              "HeaderProtectionKey", "ContentPaddingAddition")
URI_SCHEME = "vpn://"
DEFAULT_MTU = "1280"
CONTAINER = "amnezia-awg"
PROTOCOL_VERSION = "2"
QR_MAGIC = 1984                    # how the app's scanner recognises its own codes
QR_SCALE = 4                       # pixels per module: the pages rely on it
QR_BORDER = 4                      # the quiet zone the standard asks for
LENGTH_BYTES = 4


def _compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _unpadded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _padded(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def uri_json(uri: str) -> dict[str, Any]:
    """A vpn:// string opened up, its inner config too."""
    outer = json.loads(zlib.decompress(_padded(uri[len(URI_SCHEME):])[LENGTH_BYTES:]))
    outer["containers"][0]["awg"]["last_config"] = json.loads(outer["containers"][0]["awg"]["last_config"])
    return outer


def qr_frame(uri: str) -> str:
    """What the QR code for the AmneziaVPN app must hold. Its scanner does not
    read a vpn:// string: it expects the app's own frame — magic 1984, how many
    codes there are, which one this is, then the data as Qt writes a byte array
    (length, bytes) — base64 for URLs without padding. The data is the body of
    the vpn:// string. One code carries all of it (the app itself would split
    at 850 bytes, but the reader takes any size)."""
    data = _padded(uri[len(URI_SCHEME):])
    codes_total = 1
    code_index = 0
    frame = QR_MAGIC.to_bytes(2, "big") + bytes([codes_total, code_index]) + len(data).to_bytes(LENGTH_BYTES, "big") + data
    return _unpadded(frame)


def qr_png(text: str) -> str:
    buffer = io.BytesIO()
    segno.make(text, error="l", micro=False).save(buffer, kind="png", scale=QR_SCALE, border=QR_BORDER)
    return base64.b64encode(buffer.getvalue()).decode()


class DeviceConfigs:
    def __init__(self, settings: UsersSettings) -> None:
        self.__settings = settings

    def conf(self, user: User, block: InterfaceBlock, server_public: str) -> str:
        settings = self.__settings
        values = block.values()
        lines = [
            "[Interface]",
            f"PrivateKey = {user.private_key.get_secret_value()}",
            f"Address = {user.ip}/32",
            f"DNS = {settings.dns}",
            f"MTU = {values.get('MTU', DEFAULT_MTU)}",
        ]
        lines += [f"{key} = {values[key]}" for key in SHARED_FIELDS if key in values]
        lines += [
            "",
            "[Peer]",
            f"PublicKey = {server_public}",
            f"PresharedKey = {user.preshared_key.get_secret_value()}",
            f"Endpoint = {settings.endpoint}:{values.get('ListenPort', '')}",
            "AllowedIPs = " + ", ".join(settings.client_allowed),
            f"PersistentKeepalive = {settings.keepalive}",
            "",
        ]
        return "\n".join(lines)

    def uri(self, user: User, block: InterfaceBlock, server_public: str, conf: str) -> str:
        """The vpn:// string the AmneziaVPN app imports: its JSON, compressed the
        way Qt does it (length, then zlib), base64 for URLs without padding."""
        settings = self.__settings
        values = block.values()
        dns = str(settings.dns)
        inner: dict[str, object] = {key: values.get(key, "") for key in URI_FIELDS}
        inner.update({
            "allowed_ips": settings.client_allowed,
            "client_ip": str(user.ip),
            "client_ipv6": "",
            "client_priv_key": user.private_key.get_secret_value(),
            "psk_key": user.preshared_key.get_secret_value(),
            "config": conf,
            "hostName": settings.endpoint,
            "mtu": values.get("MTU", DEFAULT_MTU),
            "persistent_keep_alive": str(settings.keepalive),      # the app reads it as text
            "port": int(values.get("ListenPort", "0")),
            "server_pub_key": server_public,
        })
        awg = {
            "isThirdPartyConfig": True,
            "last_config": _compact(inner),
            "port": values.get("ListenPort", ""),
            "protocol_version": PROTOCOL_VERSION,
            "transport_proto": "udp",
        }
        outer = {
            "containers": [{"awg": awg, "container": CONTAINER}],
            "defaultContainer": CONTAINER,
            "description": settings.description,
            "dns1": dns,
            "dns2": dns,
            "hostName": settings.endpoint,
        }
        raw = _compact(outer).encode()
        return URI_SCHEME + _unpadded(len(raw).to_bytes(LENGTH_BYTES, "big") + zlib.compress(raw))

    def payload(self, user: User, block: InterfaceBlock) -> DevicePayload:
        server_public = public_of(block.values()["PrivateKey"])
        conf = self.conf(user, block, server_public)
        uri = self.uri(user, block, server_public, conf)
        return DevicePayload(name=user.name, conf=conf, uri=uri, conf_png=qr_png(conf), uri_png=qr_png(qr_frame(uri)))
