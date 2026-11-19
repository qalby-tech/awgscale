#!/usr/bin/env python3
"""A stand-in for the Telegram Bot API, for tests/stack.sh: it says yes to
everything the bot does at its start and never has an update for it.
Standard library only.   stack_telegram.py <port>"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOLD_SECONDS = 2        # how long a request for updates is kept open before "nothing new"


class Telegram(BaseHTTPRequestHandler):
    def answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        method = self.path.rsplit("/", 1)[-1].split("?")[0]
        result = True
        if method == "getMe":
            result = {"id": 1, "is_bot": True, "username": "stand_in_bot"}
        elif method == "getUpdates":
            time.sleep(HOLD_SECONDS)
            result = []
        elif method == "sendMessage":
            result = {"message_id": 1, "chat": {"id": 1}}
        body = json.dumps({"ok": True, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = answer
    do_POST = answer

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Telegram).serve_forever()
