"""Zulip source: your own account (not a bot), read and written through the REST API.

Credentials: env ZULIP_EMAIL + ZULIP_API_KEY + ZULIP_SITE, else a zuliprc file
(`[api] email, key, site`) at $MISEENPLACE_ZULIP_RC, `rc` in [sources.zulip], or ~/.zuliprc.
The key is never printed. Config (all optional) in config.toml:

    [sources.zulip]
    noise_streams = ["alerts", "deploys"]   # skipped unless a message @-mentions me
    noise_topics = ["Build Failure"]
    participation_days = 14                  # topics I posted in this recently count as mine
"""

import base64
import configparser
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.client import HTTPException, HTTPMessage
from pathlib import Path
from typing import IO, TypedDict, cast

from miseenplace.chat import Message, Target
from miseenplace.consent import Draft, Outbox
from miseenplace.model import MiseError
from miseenplace.sources.base import (
    Batch,
    Cursors,
    Person,
    SendRefused,
    SourceError,
    advance_cursor,
    fetch_batch,
)

TIMEOUT = 30


class Recipient(TypedDict):
    id: int
    email: str
    full_name: str


class ZMessage(TypedDict):
    id: int
    type: str  # "stream" | "private"
    timestamp: int
    subject: str
    content: str
    sender_full_name: str
    sender_email: str
    sender_id: int
    display_recipient: str | list[Recipient]
    flags: list[str]


class ZUser(TypedDict):
    user_id: int
    full_name: str
    email: str
    delivery_email: str | None
    is_active: bool
    is_bot: bool


@dataclass(frozen=True)
class Credentials:
    email: str
    site: str
    key: str = field(repr=False)


def https_site(site: str) -> str:
    """The key travels in every request: only https sites are accepted."""
    site = site.strip().rstrip("/")
    if not site.startswith("https://"):
        raise MiseError(
            "the Zulip site must start with https:// (the API key is sent with every call)"
        )
    return site


def credentials_from_env(env: Mapping[str, str]) -> Credentials | None:
    if all(env.get(k) for k in ("ZULIP_EMAIL", "ZULIP_API_KEY", "ZULIP_SITE")):
        return Credentials(env["ZULIP_EMAIL"], https_site(env["ZULIP_SITE"]), env["ZULIP_API_KEY"])
    return None


def credentials_from_rc(path: Path) -> Credentials:
    if not path.exists():
        raise SourceError(
            f"no Zulip credentials: set ZULIP_EMAIL, ZULIP_API_KEY and ZULIP_SITE, or create {path}"
        )
    if path.stat().st_mode & 0o077:
        print(
            f"miseenplace: warning: {path} is readable by other users; run `chmod 600 {path}`",
            file=sys.stderr,
        )
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        # the message never repeats file content: it could be the key
        raise MiseError(
            f"{path} is not a valid zuliprc; it needs an [api] section with email, key and site"
        ) from None
    if not parser.has_section("api") or not {"email", "key", "site"} <= set(parser["api"]):
        raise MiseError(f"{path}: needs an [api] section with email, key and site")
    api = parser["api"]
    return Credentials(api["email"], https_site(api["site"]), api["key"])


def load_credentials(settings: Mapping[str, object], env: Mapping[str, str]) -> Credentials:
    from_env = credentials_from_env(env)
    if from_env:
        return from_env
    rc = env.get("MISEENPLACE_ZULIP_RC") or str(settings.get("rc") or "~/.zuliprc")
    return credentials_from_rc(Path(rc).expanduser())


def string_list(settings: Mapping[str, object], key: str) -> frozenset[str]:
    return frozenset(cast(list[str], settings.get(key, [])))


class SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only within the same host: the Authorization header must not leave it."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            raise urllib.error.HTTPError(
                req.full_url, code, "redirect to another host refused", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, creds: Credentials) -> None:
        self.creds = creds
        token = base64.b64encode(f"{creds.email}:{creds.key}".encode()).decode()
        self._headers = {"Authorization": "Basic " + token}
        self._opener = urllib.request.build_opener(SameHostRedirects)

    def call(
        self, path: str, params: Mapping[str, object] | None = None, post: bool = False
    ) -> dict[str, object]:
        """One API call. A failed POST is `SendRefused` when nothing can have been posted."""
        query = urllib.parse.urlencode(params or {})
        url = f"{self.creds.site}/api/v1/{path}"
        req = (
            urllib.request.Request(url, data=query.encode(), headers=self._headers)
            if post
            else urllib.request.Request(f"{url}?{query}", headers=self._headers)
        )
        try:
            with self._opener.open(req, timeout=TIMEOUT) as r:
                body = cast(dict[str, object], json.load(r))
        except urllib.error.HTTPError as e:
            rejected = post and 400 <= e.code < 500
            raise (SendRefused if rejected else SourceError)(
                f"zulip {path}: HTTP {e.code}"
            ) from None
        except urllib.error.URLError as e:
            refused = post and isinstance(e.reason, ConnectionRefusedError | socket.gaierror)
            raise (SendRefused if refused else SourceError)(
                f"zulip unreachable ({type(e.reason).__name__}); will retry"
            ) from None
        except (TimeoutError, ConnectionError, ValueError, HTTPException) as e:
            raise SourceError(f"zulip unreachable ({type(e).__name__}); will retry") from None
        if body.get("result") != "success":
            raise SourceError(f"zulip error: {body.get('msg')}")
        return body


# ---- pure helpers over Zulip payloads ----


def key_of(m: ZMessage, me: str) -> str:
    recipient = m["display_recipient"]
    if m["type"] == "private" and isinstance(recipient, list):
        names = sorted(r["full_name"] for r in recipient if r["email"] != me)
        return "DM: " + ", ".join(names or ["me"])
    return f"#{recipient} > {m['subject']}"


def mentioned(m: ZMessage) -> bool:
    flags = m.get("flags", [])
    return "mentioned" in flags or "wildcard_mentioned" in flags


def target_of(m: ZMessage, me: str) -> Target:
    recipient = m["display_recipient"]
    if m["type"] == "private" and isinstance(recipient, list):
        return Target(",".join(str(r["id"]) for r in recipient if r["email"] != me))
    return Target(str(recipient), m["subject"])


def to_message(m: ZMessage, me: str, site: str) -> Message:
    return Message(
        id=str(m["id"]),
        where=key_of(m, me),
        sender=m["sender_full_name"],
        ts=float(m["timestamp"]),
        text=m["content"],
        mentioned=mentioned(m),
        mine=m["sender_email"] == me,
        url=f"{site}/#narrow/id/{m['id']}",
        target=target_of(m, me),
    )


def quote_reply(m: ZMessage, site: str, text: str) -> str:
    """Zulip's own quote format: the reply starts by quoting the message it answers."""
    link = f"{site}/#narrow/id/{m['id']}"
    who = f"@_**{m['sender_full_name']}|{m['sender_id']}**"
    return f"{who} [said]({link}):\n```quote\n{m['content'].strip()}\n```\n\n{text}"


class ZulipSource:
    name = "zulip"

    def __init__(
        self,
        root: Path,
        creds: Credentials,
        noise_streams: frozenset[str] = frozenset(),
        noise_topics: frozenset[str] = frozenset(),
        participation_days: float = 14,
    ) -> None:
        self.client = Client(creds)
        self.me = creds.email
        self.site = creds.site
        self.noise_streams = noise_streams
        self.noise_topics = noise_topics
        self.participation_days = participation_days
        self.cursors = Cursors(root, self.name)
        self.outbox = Outbox(root, self.name, self._deliver)

    @classmethod
    def from_config(cls, root: Path, settings: dict[str, object]) -> "ZulipSource":
        return cls(
            root,
            load_credentials(settings, os.environ),
            string_list(settings, "noise_streams"),
            string_list(settings, "noise_topics"),
            cast(float, settings.get("participation_days", 14)),
        )

    # ---- reading ----

    def _fetch(self, params: Mapping[str, object]) -> list[ZMessage]:
        d = self.client.call("messages", {"apply_markdown": "false", **params})
        return cast(list[ZMessage], d["messages"])

    def _since(
        self, cutoff: float, narrow: list[dict[str, object]] | None = None
    ) -> list[ZMessage]:
        """All messages newer than cutoff, oldest first."""
        out: dict[int, ZMessage] = {}
        anchor: int | str = "newest"
        while True:
            d = self.client.call(
                "messages",
                {
                    "anchor": anchor,
                    "num_before": 1000,
                    "num_after": 0,
                    "narrow": json.dumps(narrow or []),
                    "apply_markdown": "false",
                },
            )
            msgs = cast(list[ZMessage], d["messages"])
            out.update({m["id"]: m for m in msgs if m["timestamp"] >= cutoff})
            if not msgs or msgs[0]["timestamp"] < cutoff or d.get("found_oldest"):
                break
            anchor = msgs[0]["id"]
        return sorted(out.values(), key=lambda m: m["id"])

    def is_noise(self, m: ZMessage) -> bool:
        if m["type"] == "private" or mentioned(m):
            return False
        return m["display_recipient"] in self.noise_streams or m["subject"] in self.noise_topics

    def is_for_me(self, m: ZMessage, topics: set[tuple[str, str]]) -> bool:
        if m["sender_email"] == self.me:
            return False
        if m["type"] == "private" or mentioned(m):
            return True
        recipient = m["display_recipient"]
        return (
            isinstance(recipient, str)
            and (recipient, m["subject"]) in topics
            and not self.is_noise(m)
        )

    def my_topics(self) -> set[tuple[str, str]]:
        mine = self._since(
            time.time() - self.participation_days * 86400,
            [{"operator": "sender", "operand": self.me}],
        )
        return {
            (m["display_recipient"], m["subject"])
            for m in mine
            if m["type"] == "stream" and isinstance(m["display_recipient"], str)
        }

    def _newest(self) -> str:
        msgs = self._fetch({"anchor": "newest", "num_before": 1, "num_after": 0, "narrow": "[]"})
        return str(msgs[-1]["id"]) if msgs else "0"

    def _after(self, position: str) -> Batch:
        msgs = self._fetch(
            {
                "anchor": int(position),
                "include_anchor": "false",
                "num_before": 0,
                "num_after": 1000,
                "narrow": "[]",
            }
        )
        if not msgs:
            return Batch([], position)
        topics = self.my_topics()
        kept = [to_message(m, self.me, self.site) for m in msgs if self.is_for_me(m, topics)]
        return Batch(kept, str(msgs[-1]["id"]))

    def fetch(self, cursor: str, init: bool = False) -> Batch:
        return fetch_batch(self.cursors, cursor, init, self._newest, self._after)

    def advance(self, cursor: str, batch: Batch) -> None:
        advance_cursor(self.cursors, cursor, batch)

    def new(self, cursor: str, init: bool = False) -> list[Message]:
        batch = self.fetch(cursor, init)
        self.advance(cursor, batch)
        return batch.messages

    def digest(
        self, days: float, include_noise: bool = False, only_mine: bool = False
    ) -> list[Message]:
        msgs = self._since(time.time() - days * 86400)
        wanted = [
            m
            for m in msgs
            if (include_noise or not self.is_noise(m))
            and (not only_mine or m["sender_email"] == self.me)
        ]
        return [to_message(m, self.me, self.site) for m in wanted]

    def message(self, message_id: str) -> Message:
        if not message_id.isdigit():
            raise SourceError(f"bad zulip message id {message_id!r}")
        d = self.client.call(f"messages/{message_id}", {"apply_markdown": "false"})
        return to_message(cast(ZMessage, d["message"]), self.me, self.site)

    def thread(self, target: Target, limit: int = 40) -> list[Message]:
        if target.thread is None:
            narrow = [{"operator": "dm", "operand": self.resolve_users(target.to)}]
        else:
            narrow = [
                {"operator": "channel", "operand": target.to},
                {"operator": "topic", "operand": target.thread},
            ]
        msgs = self._fetch(
            {"anchor": "newest", "num_before": limit, "num_after": 0, "narrow": json.dumps(narrow)}
        )
        return [to_message(m, self.me, self.site) for m in msgs]

    # ---- people ----

    def _members(self) -> list[ZUser]:
        return cast(list[ZUser], self.client.call("users")["members"])

    def resolve_users(self, names: str) -> list[int]:
        """Names, emails or numeric ids -> user ids (DMs accept ids only)."""
        members = self._members()
        out: list[int] = []
        for n in (x.strip().lower() for x in names.split(",")):
            if n.isdigit():
                out.append(int(n))
                continue
            hits = [
                u["user_id"]
                for u in members
                if u["is_active"]
                and not u["is_bot"]
                and n
                in (
                    u["full_name"].lower(),
                    u["email"].lower(),
                    (u.get("delivery_email") or "").lower(),
                    u["email"].lower().split("@")[0],
                )
            ]
            if len(hits) != 1:
                raise SourceError(f"cannot resolve user {n!r}: {hits or 'no match'}")
            out.append(hits[0])
        return out

    def users(self, query: str) -> list[Person]:
        q = query.lower()
        return [
            Person(str(u["user_id"]), u["full_name"], u["email"], u["is_bot"])
            for u in self._members()
            if u["is_active"] and q in (u["full_name"] + " " + u["email"]).lower()
        ]

    # ---- writing (always through the consent gate) ----

    def draft(self, target: Target, text: str) -> Draft:
        if target.reply_to:
            m = cast(
                ZMessage,
                self.client.call(f"messages/{target.reply_to}", {"apply_markdown": "false"})[
                    "message"
                ],
            )
            return self.outbox.draft(self._reply_target(m), quote_reply(m, self.site, text))
        if target.thread is None:
            ids = ",".join(map(str, self.resolve_users(target.to)))
            return self.outbox.draft(Target(ids, None, None, f"DM: {target.to}"), text)
        label = f"#{target.to} > {target.thread}"
        return self.outbox.draft(Target(target.to, target.thread, None, label), text)

    def _reply_target(self, m: ZMessage) -> Target:
        recipient = m["display_recipient"]
        if m["type"] == "private" and isinstance(recipient, list):
            others = [r for r in recipient if r["email"] != self.me]
            ids = ",".join(str(r["id"]) for r in others)
            label = key_of(m, self.me)
            return Target(ids, None, str(m["id"]), label)
        return Target(str(recipient), m["subject"], str(m["id"]), key_of(m, self.me))

    def send(self, draft_id: str) -> str:
        return self.outbox.send(draft_id)

    def _deliver(self, target: Target, text: str) -> str:
        try:
            params = self._post_params(target, text)
        except SourceError as e:  # resolving names only reads: nothing was posted
            raise SendRefused(str(e)) from None
        return str(self.client.call("messages", params, post=True)["id"])

    def _post_params(self, target: Target, text: str) -> dict[str, object]:
        if target.thread is None:
            ids = self.resolve_users(target.to)
            return {"type": "direct", "to": json.dumps(ids), "content": text}
        return {"type": "stream", "to": target.to, "topic": target.thread, "content": text}
