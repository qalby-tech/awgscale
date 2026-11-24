"""QR pictures the pages show: PNG, base64, four pixels per module (the pages rely on it) and the quiet
zone the standard asks for."""
from __future__ import annotations

import base64
import io

import segno

QR_SCALE = 4                       # pixels per module: the pages rely on it
QR_BORDER = 4                      # the quiet zone the standard asks for


def qr_png(text: str) -> str:
    buffer = io.BytesIO()
    segno.make(text, error="l", micro=False).save(buffer, kind="png", scale=QR_SCALE, border=QR_BORDER)
    return base64.b64encode(buffer.getvalue()).decode()
