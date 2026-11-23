#!/usr/bin/env python3
"""A stand-in for the panel's invitation (panel/routes/invites.py) over HTTPS, for app/tests/android.sh only:
what a link is for (GET /i/<secret>/info) and a device for it (POST /i/<secret>, the header X-Invite: 1),
which gets the config of testserver's client. Made up; the answers have the panel's shapes.

    panel_standin.py <address> <certificate> <key> <client.conf>
"""
import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

ADDRESS, CERTIFICATE, KEY, CONF = sys.argv[1:5]
SECRET = "Zx3kQ0aB1cD2eF3gH4iJ5k"
STATE = {"left": 1}


class Handler(BaseHTTPRequestHandler):
    def answer(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != f"/i/{SECRET}/info" or STATE["left"] == 0:
            return self.answer(404, {"error": "not found"})
        print("info", flush=True)
        self.answer(200, {"group": "Дом", "left": STATE["left"], "total": 1, "expires_at": None})

    def do_POST(self):
        if self.path != f"/i/{SECRET}" or STATE["left"] == 0:
            return self.answer(404, {"error": "not found"})
        if self.headers.get("X-Invite") != "1":
            return self.answer(403, {"error": "no header"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        STATE["left"] = 0
        print("redeem", body.get("name"), flush=True)
        with open(CONF) as conf:
            self.answer(200, {"name": body.get("name"), "conf": conf.read(), "group": "Дом", "owner": True})

    def log_message(self, *args):
        pass


server = HTTPServer((ADDRESS, 443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(CERTIFICATE, KEY)
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
