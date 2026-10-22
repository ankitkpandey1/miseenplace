"""`miseenplace demo`: a two-minute guided tour on fictional data, no account needed."""

import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from miseenplace.assets import demo_data_dir
from miseenplace.consent import ConsentError, Target
from miseenplace.inbox import Inbox, render_lines, title_of, topic_of
from miseenplace.model import MiseError, top
from miseenplace.render import line, standup
from miseenplace.sources.demo import DemoSource
from miseenplace.store import Store


def seed(root: Path) -> None:
    """Copy the fictional inbox into <root>/demo with absolute timestamps, cursor at the start."""
    data = json.loads((demo_data_dir() / "inbox.json").read_text())
    now = time.time()
    for m in data["messages"]:
        m["ts"] = now - m.pop("minutes_ago") * 60
    (root / "demo").mkdir(parents=True, exist_ok=True)
    (root / "demo" / "inbox.json").write_text(json.dumps(data, indent=1))
    DemoSource(root).cursors.set("demo", "0")


def step(say: Callable[[str], None], title: str, cmd: str) -> None:
    say(f"\n=== {title}\n$ {cmd}")


def run_tour(root: Path, say: Callable[[str], None] = print) -> None:
    seed(root)
    store, src = Store(root), DemoSource(root)
    inbox = Inbox(root, src)
    step(
        say,
        "1. Only what needs you (my own earlier answer hides the first ask)",
        "miseenplace inbox --cursor demo",
    )
    inbox.ingest("demo")
    say(render_lines(inbox.open_items()))
    say(
        "Message 1 asked who owns the changelog, but you already answered in that thread, so it"
        " is not listed. The CLI checked; the agent did not have to."
    )

    step(say, "2. Bookkeeping in one call each", "miseenplace triage 3 --todo   (and 4)")
    for item_id in ("3", "4"):
        item = inbox.get(item_id)
        created = store.add(title_of(item), "mine", None, item.who, None, topic=topic_of(item))
        ref = f"#{created['id']}"
        inbox.set_status(item, "todo", ref)
        say(f"todo {ref}")

    step(
        say,
        "3. A reply is a draft first. Nothing is posted.",
        "miseenplace triage 5 --reply-draft 'sending it Thursday'",
    )
    d = src.draft(Target("", reply_to="5"), "sending it Thursday")
    inbox.set_status(inbox.get("5"), "drafted", d.id)
    say(d.preview())
    say("A human reads this exact text. Only an explicit yes on it justifies the next step.")

    step(say, "4. Free text can never be sent", "miseenplace send 'hello'")
    try:
        src.send("hello")
    except ConsentError as e:
        say(f"miseenplace: {e}")

    step(say, "5. The human said yes to that exact text", f"miseenplace send {d.id}")
    say(f"posted {src.send(d.id)}")
    step(say, "6. Sending twice is refused", f"miseenplace send {d.id}")
    try:
        src.send(d.id)
    except MiseError as e:
        say(f"miseenplace: {e}")

    store.log("triaged the inbox with miseenplace", spent="10m")
    step(
        say,
        "7. What first, and the stand-up written from the log",
        "miseenplace top && miseenplace standup --since today",
    )
    for i in top(store.load()[1]):
        say(line(i))
    say(standup(store.load()[1], store.read_log(), "today"))
    say(
        f"\nTry it yourself:\n  export MISEENPLACE_DIR={root} MISEENPLACE_SOURCE=demo\n"
        "  miseenplace top\n  miseenplace thread --channel platform --topic 'release 2.4'"
    )


def tour(dir_: str | None) -> None:
    root = Path(dir_) if dir_ else Path(tempfile.mkdtemp(prefix="miseenplace-demo-"))
    if root.exists() and any(root.iterdir()):
        raise MiseError(f"{root} is not empty; pick a new directory for the demo")
    root.mkdir(parents=True, exist_ok=True)
    run_tour(root)
