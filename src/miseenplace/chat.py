"""The two small types every layer shares: where a message goes and what a message is."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    """Where a message goes. `label` is for humans only; sources fill it when they resolve."""

    to: str  # channel name, or comma-separated people for a direct message
    thread: str | None = None  # topic/thread inside a channel; None means a direct message
    reply_to: str | None = None  # id of the message being answered, if any
    label: str = ""
    edit_of: str | None = None  # id of my message this replaces: `send` edits it instead of posting


@dataclass(frozen=True)
class Message:
    id: str
    where: str  # human label of the thread: "#channel > topic" or "DM: names"
    sender: str
    ts: float  # unix seconds
    text: str
    mentioned: bool = False  # the message names me (or a group I am in)
    mine: bool = False  # I wrote it; `new` never returns these
    url: str | None = None
    target: Target | None = None  # where a reply to this message goes; None if unknown
