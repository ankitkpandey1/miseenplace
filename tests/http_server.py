"""A real HTTP server on localhost for tests that need real sockets (timeouts, redirects).

It is not patched into the code under test: the client opens a real connection to it, exactly as
it would to a Zulip site. `mode` decides how POST /api/v1/messages ends.
"""

import json
import re
import threading
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HANG_SECONDS = 2.0


@dataclass
class Seen:
    # ok | reject | hang | truncated | redirect | expired (PATCH refused) | down (GET answers 500)
    mode: str = "ok"
    redirect_to: str = ""
    posts: list[dict[str, list[str]]] = field(default_factory=list)
    authorization: list[str | None] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)  # every GET path seen
    messages: list[dict[str, object]] = field(default_factory=list)  # served by GET /messages
    unreadable: list[int] = field(default_factory=list)  # GET refuses these, PATCH accepts them
    patches: list[tuple[str, dict[str, list[str]]]] = field(
        default_factory=list
    )  # PATCH path, body


def handler_for(seen: Seen) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return None

        def reply(self, code: int, body: bytes, declared: int | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) if declared is None else declared))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            seen.authorization.append(self.headers.get("Authorization"))
            seen.paths.append(self.path)
            if seen.mode == "down":
                self.reply(500, b"<html>Internal Server Error</html>")
                return
            if seen.mode == "redirect":
                self.send_response(302)
                self.send_header("Location", seen.redirect_to)
                self.end_headers()
                return
            one = re.fullmatch(r"/api/v1/messages/(\d+)", self.path.split("?")[0])
            if one:
                found = [m for m in seen.messages if m["id"] == int(one[1])]
                # the shape Zulip answers with for an id that does not exist or is not readable
                if not found or int(one[1]) in seen.unreadable:
                    err = {"result": "error", "msg": "Invalid message(s)", "code": "BAD_REQUEST"}
                    self.reply(400, json.dumps(err).encode())
                    return
                self.reply(200, json.dumps({"result": "success", "message": found[0]}).encode())
                return
            body = {"result": "success", "members": [], "messages": seen.messages}
            self.reply(200, json.dumps({**body, "found_oldest": True}).encode())

        def do_PATCH(self) -> None:
            seen.authorization.append(self.headers.get("Authorization"))
            size = int(self.headers.get("Content-Length", 0))
            seen.patches.append((self.path, urllib.parse.parse_qs(self.rfile.read(size).decode())))
            if seen.mode == "expired":  # Zulip's wording when the realm's edit limit has passed
                msg = "The time limit for editing this message has passed"
                err = {"result": "error", "msg": msg, "code": "BAD_REQUEST"}
                self.reply(400, json.dumps(err).encode())
            elif not self.editable():
                err = {"result": "error", "msg": "Invalid message(s)", "code": "BAD_REQUEST"}
                self.reply(400, json.dumps(err).encode())
            else:
                self.reply(200, b'{"result":"success","msg":""}')

        def editable(self) -> bool:
            known = [int(m["id"]) for m in seen.messages] + seen.unreadable  # type: ignore[call-overload]
            return int(self.path.rsplit("/", 1)[1]) in known

        def do_POST(self) -> None:
            seen.authorization.append(self.headers.get("Authorization"))
            size = int(self.headers.get("Content-Length", 0))
            seen.posts.append(urllib.parse.parse_qs(self.rfile.read(size).decode()))
            if seen.mode == "reject":
                self.reply(400, b'{"result":"error","msg":"bad stream"}')
            elif seen.mode == "hang":  # accepted, but the answer comes too late
                time.sleep(HANG_SECONDS)
                self.reply(200, b'{"result":"success","id":%d}' % (len(seen.posts) + 100))
            elif seen.mode == "truncated":  # accepted, but the answer is cut off
                self.reply(200, b'{"result":"succ', declared=200)
                self.close_connection = True
            else:
                self.reply(200, b'{"result":"success","id":%d}' % (len(seen.posts) + 100))

    return Handler


@contextmanager
def serve(seen: Seen) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(seen))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
