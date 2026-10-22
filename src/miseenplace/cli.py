"""Command line: parses arguments, then calls the store, the inbox and a source."""

import argparse
import os
import sys
import time
from functools import partial
from pathlib import Path
from typing import cast

from miseenplace import __version__, lessons, lock, personal, reconcile, today, update
from miseenplace.assets import skills_dir
from miseenplace.chat import Target
from miseenplace.config import CONFIG_NAME, load_config, resolve_root
from miseenplace.consent import mark_draft, pending_drafts
from miseenplace.inbox import (
    Inbox,
    InboxItem,
    render_json,
    render_lines,
    title_of,
    topic_of,
)
from miseenplace.model import (
    CLOSED,
    KINDS,
    STATES,
    Extras,
    Item,
    MiseError,
    eta_key,
    priority_key,
    select,
    top,
)
from miseenplace.render import brief, diary_line, format_message, line, standup
from miseenplace.sources import open_source
from miseenplace.sources.base import Digestible, Lookup, Source, SourceError
from miseenplace.storage.base import Storage, StorageError
from miseenplace.store import Store
from miseenplace.tour import tour
from miseenplace.watch import watch

CONFIG_TEMPLATE = """\
# miseenplace config. Personal: keep this file out of public repositories.
actors = ["me", "agent"]        # who may write; --by picks one
default_actor = "agent"
# default_source = "zulip"      # or set MISEENPLACE_SOURCE
# auto_update = false           # skip the daily background update check

# [storage]
# backend = "git"               # local (default) | git | "package.module:Class"
# remote = "git@example.com:me/my-miseenplace-data.git"

# [sources.zulip]
# noise_streams = ["alerts"]    # skipped unless a message @-mentions me
# noise_topics = ["Build Failure"]
# participation_days = 14
"""
SOURCE_CMDS = (
    "new", "thread", "digest", "today", "draft", "drafts", "send", "users", "inbox", "triage",
    "watch",
)  # fmt: skip


# ---- parser -------------------------------------------------------------------


MIN_INTERVAL = 10.0


def interval(text: str) -> float:
    try:
        seconds = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds") from None
    if seconds < MIN_INTERVAL:
        raise argparse.ArgumentTypeError(f"must be at least {MIN_INTERVAL:g} seconds, got {text}")
    return seconds


def add_work_args(s: argparse.ArgumentParser) -> None:
    s.add_argument("--topic", help="group items under a topic; '' clears")
    s.add_argument("--context", help="1-3 lines: why this exists, who asked; '' clears")
    s.add_argument("--next", help="where you left off / the next concrete step; '' clears")
    s.add_argument("--blocker", help="who or what it is waiting on; '' clears")
    s.add_argument("--think", help="slug of a longer-form notebook for big work; '' clears")
    s.add_argument("--priority", help="p0 (highest) .. p3, or none")
    s.add_argument("--spent", help="time to ADD to the total: 30m, 1h or 1h30m")
    s.add_argument(
        "--decision",
        dest="decisions",
        action="append",
        metavar="WHAT::WHY",
        help="append a decision (repeatable); decisions are never rewritten",
    )
    s.add_argument("--tag", dest="tags", action="append", help="add a tag (repeatable)")
    s.add_argument("--untag", dest="untags", action="append", help="remove a tag (repeatable)")


def add_write_args(s: argparse.ArgumentParser) -> None:
    s.add_argument("--by", help=f"actor to attribute the write to ({CONFIG_NAME}); default_actor")
    s.add_argument("--no-sync", action="store_true", help="skip pull/render/commit/push")


def add_target_args(s: argparse.ArgumentParser) -> None:
    s.add_argument("--channel", help="channel/stream name (with --topic)")
    s.add_argument("--topic", help="topic/thread inside the channel")
    s.add_argument("--dm", help="direct message: comma-separated names, handles or ids")


def add_journal_parsers(
    sub: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    ad = sub.add_parser("add", help="add an action item")
    ad.add_argument("title")
    ad.add_argument("--kind", choices=KINDS, default="mine", help="mine | waiting | reminder")
    st = sub.add_parser("set", help="change an item: state, eta, notes, work context")
    st.add_argument("item", help="id or unique title substring of an open item")
    st.add_argument("--state", choices=STATES)
    dn = sub.add_parser("done", help="close an item as done")
    dr = sub.add_parser("drop", help="close an item as dropped")
    for s in (dn, dr):
        s.add_argument("item", help="id or unique title substring of an open item")
    for s in (ad, st):
        s.add_argument("--eta", help="YYYY-MM-DD, MM-DD, today, tomorrow, +Nd, mon..sun or none")
        s.add_argument("--who", help="counterparty: who asked, or who is waited on")
        s.add_argument("--ref", action="append", help="link (http/https) to attach; repeatable")
        add_work_args(s)
    for s in (ad, st, dn, dr):
        s.add_argument("--note", help="append a note")
        add_write_args(s)
    lg = sub.add_parser("log", help="diary entry not tied to an item")
    lg.add_argument("text")
    lg.add_argument("--topic")
    lg.add_argument("--tag", dest="tags", action="append", help="repeatable")
    lg.add_argument("--spent", help="30m, 1h or 1h30m")
    lg.add_argument("--ref", action="append", help="link (http/https); repeatable")
    add_write_args(lg)
    ls = sub.add_parser("list", help="list items, optionally filtered")
    ls.add_argument("--all", action="store_true", help="include done and dropped")
    ls.add_argument("--topic", help="exact topic, case-insensitive")
    ls.add_argument("--tag")
    ls.add_argument("--priority", help="p0..p3")
    ls.add_argument("--kind", choices=KINDS)
    ls.add_argument("--who", help="substring, case-insensitive")
    ls.add_argument("--sort", choices=("eta", "priority"), default="eta", help="eta or priority")
    tp = sub.add_parser("top", help="open items of kind mine by priority, then eta")
    tp.add_argument("-n", type=int, default=5, help="how many (default 5)")
    br = sub.add_parser("brief", help="full view of one item, or every open item in a topic")
    br.add_argument("key", help="item id, topic, or unique title substring")
    rc = sub.add_parser("reconcile", help="propose state changes for open items from their sources")
    rc.add_argument("--apply", action="store_true", help="make the changes (default: print only)")
    rc.add_argument("--sync", action="store_true", help="with --apply: run sync afterwards")
    rc.add_argument("--by", help="actor to attribute the writes to")
    sub.add_parser("sync", help="pull, render Action Items.md, publish (storage backend decides)")
    su = sub.add_parser("standup", help="Did / Next / Waiting on")
    su.add_argument("--since", default="yesterday", help="yesterday, today, -Nd or YYYY-MM-DD")


def add_inbox_parsers(
    sub: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    ib = sub.add_parser("inbox", help="ONLY the actionable new messages, one line each")
    ib.add_argument("--cursor", default="inbox", help="this consumer's own read position")
    ib.add_argument("--json", action="store_true", help="machine-readable output")
    ib.add_argument("--all", action="store_true", help="also show auto-ignored and handled")
    ib.add_argument("--verbose", action="store_true", help="banner and full text, for humans")
    tr = sub.add_parser("triage", help="handle one inbox item in one call")
    tr.add_argument("id", help="the id from `inbox`")
    tr.add_argument("--todo", action="store_true", help="add a journal item (kind mine)")
    tr.add_argument("--waiting", action="store_true", help="add a journal item (waiting on them)")
    tr.add_argument("--ignore", action="store_true", help="nothing to do")
    tr.add_argument("--reply-draft", metavar="TEXT", help="store a quote-reply; nothing is posted")
    tr.add_argument("--eta", help="for --todo/--waiting")
    tr.add_argument("--topic", help="for --todo/--waiting; default: the thread's topic")
    tr.add_argument("--no-sync", action="store_true")
    wt = sub.add_parser("watch", help="block until a new actionable item, print it, exit 0")
    wt.add_argument("--cursor", default="watcher", help="own read position (default watcher)")
    wt.add_argument(
        "--interval",
        type=interval,
        default=300,
        help=f"seconds between polls (min {MIN_INTERVAL:g})",
    )
    wt.add_argument("--once", action="store_true", help="one poll, then exit 0 (cron, launchd)")
    wt.add_argument("--max-cycles", type=int, default=0, help="stop with exit 3 after N polls")
    n = sub.add_parser("new", help="raw new messages for me since this cursor; silent if none")
    n.add_argument("--cursor", default="default", help="this consumer's own read position")
    n.add_argument("--init", action="store_true", help="reset the cursor to now")
    n.add_argument("--quiet", action="store_true")
    n.add_argument("--width", type=int, default=300)
    t = sub.add_parser("thread", help="read a thread or a DM conversation")
    add_target_args(t)
    t.add_argument("-n", type=int, default=40)
    t.add_argument("--width", type=int, default=2000)
    dg = sub.add_parser("digest", help="threads of the last N days, grouped (for humans)")
    dg.add_argument("--days", type=float, default=5)
    dg.add_argument("--all", action="store_true", help="include noise channels")
    dg.add_argument("--mine", action="store_true", help="only my own messages")
    dg.add_argument("--width", type=int, default=1200)
    td = sub.add_parser(
        "today", help="threads of the last N days that involve me, awaiting-you first"
    )
    td.add_argument("--days", type=float, default=1)
    td.add_argument("--width", type=int, default=100)
    d = sub.add_parser("draft", help="store the exact text of a message; nothing is posted")
    add_target_args(d)
    d.add_argument("--reply-to", help="id of the message to quote-reply to")
    d.add_argument("text", help="message text, or - to read stdin")
    sub.add_parser("drafts", help="list drafts not yet sent")
    s = sub.add_parser("send", help="post a stored draft; takes only a draft id")
    s.add_argument("draft_id", help="id printed by `draft`, after the human said yes to its text")
    verdict = s.add_mutually_exclusive_group()
    verdict.add_argument("--mark-sent", action="store_true", help="after checking: it WAS posted")
    verdict.add_argument(
        "--mark-unsent", action="store_true", help="after checking: it was NOT posted"
    )
    u = sub.add_parser("users", help="look up people")
    u.add_argument("query")


def add_personal_parsers(
    sub: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    nr = sub.add_parser("note-request", help="log the user's exact words to the request log")
    nr.add_argument("text", help="verbatim text, or - to read stdin")
    nr.add_argument("--no-sync", action="store_true")
    rq = sub.add_parser("requests", help="read back the request log, newest last")
    rq.add_argument("-n", type=int, default=10)
    sy = sub.add_parser("style", help="the user's writing style and sent-message corpus")
    ss = sy.add_subparsers(dest="style_cmd", required=True, metavar="action")
    ss.add_parser("show", help="print the style file for the source")
    sl = ss.add_parser("learn", help="fetch my sent messages into the corpus (dedup by id)")
    sl.add_argument("--days", type=float, default=90)
    sp = ss.add_parser("sample", help="print my most recent sent messages from the corpus")
    sp.add_argument("-n", type=int, default=200)
    st = ss.add_parser("set", help="write the style file from text (use - for stdin)")
    st.add_argument("text")
    for p in (sl, st):
        p.add_argument("--no-sync", action="store_true")
    add_lessons_parser(sub)


def positive(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number of at least 1, got {text!r}")
    return int(text)


def add_lessons_parser(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    ls = sub.add_parser("lessons", help="lessons from corrections: list, add, vote, case, check")
    act = ls.add_subparsers(dest="lessons_cmd", required=True, metavar="action")
    li = act.add_parser("list", help="one line per lesson")
    li.add_argument("--full", action="store_true", help="add the evidence")
    li.add_argument("--width", type=positive, default=120)
    ad = act.add_parser("add", help="save a lesson (starts at 2 votes)")
    ad.add_argument("--when", required=True)
    ad.add_argument("--do", required=True)
    ad.add_argument("--not", dest="not_", required=True)
    ad.add_argument("--evidence", required=True)
    ad.add_argument("--pattern", default="", help="regex that matches the wrong action")
    vo = act.add_parser("vote", help="re-confirm (up) or contradict (down); 0 deletes")
    vo.add_argument("id", type=int)
    vo.add_argument("direction", choices=("up", "down"))
    ca = act.add_parser("case", help="record a past correction for the weekly replay")
    ca.add_argument("--wrong", required=True)
    ca.add_argument("--fine", required=True)
    ck = act.add_parser("check", help="replay corrections; revert the lessons if they got worse")
    for p in (ad, vo, ca, ck):
        p.add_argument("--no-sync", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="miseenplace",
        description="Every ask, in its place. Start with: miseenplace inbox. See docs/cli.md.",
        epilog="Environment: MISEENPLACE_DIR (data root), MISEENPLACE_SOURCE (default source).",
    )
    p.add_argument("--version", action="version", version=f"miseenplace {__version__}")
    p.add_argument(
        "--dir",
        help="data root (default $MISEENPLACE_DIR or ~/.local/share/miseenplace)",
    )
    p.add_argument("--source", help="chat source: zulip, demo (default $MISEENPLACE_SOURCE)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")
    add_journal_parsers(sub)
    add_inbox_parsers(sub)
    add_personal_parsers(sub)
    sub.add_parser("init", help="create the data root and a config template")
    dm = sub.add_parser("demo", help="two-minute guided tour on fictional data, no account")
    dm.add_argument("--keep-dir", dest="demo_dir", help="run in this empty directory")
    sk = sub.add_parser("skills", help="print the path of the bundled agent skills, or copy them")
    chosen = sk.add_mutually_exclusive_group()
    chosen.add_argument("--install", metavar="DIR", help="copy them into DIR; kept fresh on update")
    chosen.add_argument("--refresh", action="store_true", help="re-copy into every installed DIR")
    sub.add_parser("update", help="check PyPI now and upgrade if there is a newer version")
    return p


# ---- journal commands -----------------------------------------------------------


def work_options(a: argparse.Namespace) -> Extras:
    keys = (
        "topic", "context", "next", "blocker", "think", "priority", "spent",
        "decisions", "tags", "untags",
    )  # fmt: skip
    return cast(Extras, {k: v for k in keys if (v := getattr(a, k, None)) is not None})


def run_list(store: Store, a: argparse.Namespace) -> None:
    rows = select(store.load()[1], a.all, a.topic, a.tag, a.priority, a.kind, a.who)
    for i in sorted(rows, key=priority_key if a.sort == "priority" else eta_key):
        print(f"[{i['kind']}] " + line(i))


def run_write(store: Store, a: argparse.Namespace) -> None:
    if a.cmd == "log":
        entry = store.log(a.text, a.topic, a.tags, a.spent, a.ref, a.by)
        print(diary_line(entry))
        label = f"log ({entry['by']})"
    else:
        if a.cmd == "add":
            item = store.add(a.title, a.kind, a.eta, a.who, a.ref, a.note, a.by, **work_options(a))
        else:
            item = store.update(
                a.item, a.cmd, a.by,
                getattr(a, "state", None), getattr(a, "eta", None),
                getattr(a, "who", None), getattr(a, "ref", None),
                a.note, **work_options(a),
            )  # fmt: skip
        print(line(item))
        label = f"{a.cmd} #{item['id']} ({store.actor(a.by)})"
    if not a.no_sync:
        store.sync(label)


def run_init(store: Store, a: argparse.Namespace) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    config = store.root / CONFIG_NAME
    if not config.exists():
        config.write_text(CONFIG_TEMPLATE)
    print(f"ready: {store.root}")


def run_journal(store: Store, a: argparse.Namespace) -> None:
    if a.cmd == "standup":
        print(standup(store.load()[1], store.read_log(), a.since))
    elif a.cmd == "list":
        run_list(store, a)
    elif a.cmd == "top":
        for i in top(store.load()[1], a.n):
            print(line(i))
    elif a.cmd == "brief":
        print(brief(store.load()[1], a.key))
    elif a.cmd == "sync":
        store.sync()
    elif a.cmd == "init":
        run_init(store, a)
    else:
        run_write(store, a)


# ---- inbox commands -------------------------------------------------------------


def target_of(a: argparse.Namespace) -> Target:
    if getattr(a, "reply_to", None):
        return Target("", None, a.reply_to)
    if a.dm:
        return Target(a.dm)
    if a.channel and a.topic:
        return Target(a.channel, a.topic)
    raise MiseError("name a destination: --dm NAMES, or --channel C --topic T (or --reply-to ID)")


def read_text(text: str) -> str:
    return sys.stdin.read() if text == "-" else text


def run_digest(src: Source, a: argparse.Namespace) -> None:
    if not isinstance(src, Digestible):
        raise MiseError(f"source {src.name!r} does not offer digest; use inbox/thread")
    groups: dict[str, list[str]] = {}
    for m in src.digest(a.days, a.all, a.mine):
        groups.setdefault(m.where, []).append(format_message(m, a.width))
    print(f"# {sum(map(len, groups.values()))} messages in {a.days:g}d, {len(groups)} threads")
    for where, rows in groups.items():
        print(f"\n===== {where}")
        print("\n".join(rows))


def run_today(src: Source, a: argparse.Namespace) -> None:
    if not isinstance(src, Digestible):
        raise MiseError(f"source {src.name!r} does not offer today; use inbox/thread")
    print(today.render(today.involving_me(src.digest(a.days, include_noise=True)), a.days, a.width))


def open_lookup(name: str | None, store: Store) -> Lookup:
    return reconcile.lookup_of(open_source(name, store.root, store.config))


def run_reconcile(store: Store, a: argparse.Namespace) -> None:
    if a.sync and not a.apply:
        raise MiseError("--sync goes with --apply")
    items = [i for i in store.load()[1] if i["state"] not in CLOSED]
    chat = partial(open_lookup, source_name(a), store)
    changes, notes = reconcile.propose(items, chat, reconcile.pr_checker())
    for note in notes:
        print(f"miseenplace: {note}", file=sys.stderr)
    if a.apply:
        print(reconcile.report(changes, True) or "0 changes")
        print(f"applied {reconcile.apply(store, changes, a.by)}")
        if a.sync:
            store.sync("reconcile")
    else:
        print(reconcile.report(changes, False))


def run_new(src: Source, a: argparse.Namespace) -> None:
    """The cursor moves only after every line has been written out."""
    batch = src.fetch(a.cursor, a.init)
    if a.init and not a.quiet:
        print(f"baseline set for cursor {a.cursor!r}")
    for m in batch.messages:
        print(f"{m.where} | {format_message(m, a.width)}".replace("\n", " "))
    sys.stdout.flush()
    src.advance(a.cursor, batch)


def run_inbox(store: Store, src: Source, a: argparse.Namespace) -> None:
    inbox = Inbox(store.root, src)
    inbox.ingest(a.cursor)
    try:
        inbox.recheck()  # drop asks the user answered meanwhile, e.g. in the chat app itself
    except SourceError as e:
        print(f"miseenplace: could not re-check answered threads ({e})", file=sys.stderr)
    items = inbox.items() if a.all else inbox.open_items()
    if a.json:
        print(render_json(items))
    elif a.verbose:
        print(f"# {len(items)} open")
        for i in items:
            print(f"{i.line()}\n    {' '.join(i.text.split())}\n    {i.url or ''}")
    elif items:
        print(render_lines(items))
    sys.stdout.flush()
    inbox.mark_shown([i for i in items if i.status == "open"], a.cursor)


HANDLED = ("todo", "waiting", "ignored", "answered")


def filed_item(store: Store, mark: str) -> Item | None:
    """The journal item this inbox message already produced, if any (retry safety)."""
    return next((i for i in store.load()[1] if mark in (i["context"] or "")), None)


def triage_journal(store: Store, src_name: str, item: InboxItem, a: argparse.Namespace) -> str:
    mark = f"inbox:{src_name}:{item.id}"
    existing = filed_item(store, mark)
    if existing:  # an earlier attempt added it but did not finish: reuse, never duplicate
        return f"#{existing['id']}"
    extra = cast(
        Extras,
        {
            "topic": a.topic or topic_of(item),
            "context": f"{item.who} in {item.where}: {item.ask()} [{mark}]",
        },
    )
    kind = "waiting" if a.waiting else "mine"
    refs = [item.url] if item.url else None
    created = store.add(title_of(item), kind, a.eta, item.who, refs, **extra)
    return f"#{created['id']}"


def run_triage(store: Store, src: Source, a: argparse.Namespace) -> None:
    actions = [a.todo, a.waiting, a.ignore, bool(a.reply_draft)]
    if not any(actions):
        raise MiseError("say what to do: --todo, --waiting, --ignore or --reply-draft TEXT")
    if sum(actions) > 1:
        raise MiseError("pick one of --todo, --waiting, --ignore, --reply-draft")
    if (a.ignore or a.reply_draft) and (a.eta or a.topic):
        raise MiseError("--eta and --topic only go with --todo or --waiting")
    inbox = Inbox(store.root, src)
    item = inbox.get(a.id)
    if item.status in HANDLED:
        raise MiseError(f"inbox item {item.id} is already {item.status} {item.ref}".rstrip())
    if a.ignore:
        inbox.set_status(item, "ignored")
        print(f"ignored {item.id}")
    if a.todo or a.waiting:
        ref = triage_journal(store, src.name, item, a)
        inbox.set_status(item, "waiting" if a.waiting else "todo", ref)
        print(f"{'waiting' if a.waiting else 'todo'} {ref}")
        if not a.no_sync:
            store.sync(f"triage {item.id}")
    if a.reply_draft:
        d = src.draft(Target("", reply_to=item.id), read_text(a.reply_draft))
        inbox.set_status(item, "drafted", d.id)
        print(d.preview())
        print(f"send: miseenplace send {d.id}   (only after the human says yes to this text)")


def run_source_cmd(store: Store, a: argparse.Namespace) -> None:
    if a.cmd == "watch":  # opens its own source each cycle: a bad login is logged, not fatal
        make = partial(open_source, source_name(a), store.root, store.config)
        raise SystemExit(watch(store.root, make, a.cursor, a.interval, a.once, a.max_cycles))
    src = open_source(source_name(a), store.root, store.config)
    if a.cmd == "inbox":
        run_inbox(store, src, a)
    elif a.cmd == "triage":
        run_triage(store, src, a)
    elif a.cmd == "new":
        run_new(src, a)
    elif a.cmd == "thread":
        for m in src.thread(target_of(a), a.n):
            print(format_message(m, a.width))
    elif a.cmd == "digest":
        run_digest(src, a)
    elif a.cmd == "today":
        run_today(src, a)
    elif a.cmd == "draft":
        d = src.draft(target_of(a), read_text(a.text))
        print(d.preview())
        print(f"send: miseenplace send {d.id}   (only after the human says yes to this text)")
    elif a.cmd == "drafts":
        for d in pending_drafts(store.root, src.name):
            print(d.preview())
    elif a.cmd == "send" and (a.mark_sent or a.mark_unsent):
        d = mark_draft(store.root, src.name, a.draft_id, a.mark_sent)
        print(f"draft {d.id} marked {'sent' if a.mark_sent else 'unsent (sendable again)'}")
    elif a.cmd == "send":
        print(f"sent {src.name} message {src.send(a.draft_id)}")
    else:
        for p in src.users(a.query):
            print(f"{p.name}\t{p.handle}\tbot={p.bot}")


# ---- personal data: requests and style ---------------------------------------------


def source_name(a: argparse.Namespace) -> str | None:
    return cast(str | None, a.source or os.environ.get("MISEENPLACE_SOURCE"))


def run_note_request(store: Store, a: argparse.Namespace) -> None:
    r = personal.note_request(store.storage, read_text(a.text), source_name(a))
    print(f"noted {r.ts}")
    if not a.no_sync:
        store.sync("note-request")


def run_requests(store: Store, a: argparse.Namespace) -> None:
    for r in personal.read_requests(store.storage, a.n):
        print(f"{r.ts}\t{r.source or '-'}\t{r.text}")


def run_style(store: Store, a: argparse.Namespace) -> None:
    src = open_source(source_name(a), store.root, store.config)
    name = src.name
    if a.style_cmd == "show":
        print(
            personal.read_style(store.storage, name) or f"no style yet for {name}: run learn-style"
        )
    elif a.style_cmd == "learn":
        added, total = personal.learn(store.storage, src, a.days)
        print(f"corpus {name}: +{added}, {total} total")
        if not a.no_sync:
            store.sync("style learn")
    elif a.style_cmd == "sample":
        for s in personal.sample(store.storage, name, a.n):
            print(" ".join(s.text.split()))
    else:
        personal.write_style(store.storage, name, read_text(a.text))
        print(f"wrote {personal.style_name(name)}")
        if not a.no_sync:
            store.sync("style set")


def run_lessons(store: Store, a: argparse.Namespace) -> None:
    if a.lessons_cmd == "list":
        found = lessons.read_lessons(store.storage)
        print("\n".join(x.line(a.width, a.full) for x in found) or "0 lessons")
        return
    with lock.exclusive(store.root):  # a cron `check` never overlaps an add or a vote
        change_lessons(store.storage, a)
    if not a.no_sync:
        store.sync(f"lessons {a.lessons_cmd}")


def change_lessons(st: Storage, a: argparse.Namespace) -> None:
    cmd = a.lessons_cmd
    if cmd == "add":
        new = lessons.add_lesson(st, a.when, a.do, a.not_, a.evidence, a.pattern)
        print(f"L{new.id} v{new.votes}")
    elif cmd == "vote":
        print(lessons.vote(st, a.id, 1 if a.direction == "up" else -1))
    elif cmd == "case":
        lessons.add_case(st, a.wrong, a.fine)
        print(f"{len(lessons.read_cases(st))} corrections")
    else:
        print(lessons.check(st))


# ---- entry --------------------------------------------------------------------


READ_CMDS = ("list", "top", "brief", "standup", "reconcile")
NO_AUTO_UPDATE = ("send", "update", "skills")


def refresh_for_read(store: Store) -> None:
    """Pull before reading; offline is not fatal for a read, so say so and use the local copy."""
    try:
        store.refresh()
    except StorageError as e:
        print(f"miseenplace: using local copy ({e})", file=sys.stderr)


def run_skills(a: argparse.Namespace) -> None:
    if a.install:
        n = update.install_skills(Path(a.install), update.cache_dir())
        print(f"installed {n} skills into {Path(a.install).expanduser()}")
    elif a.refresh:
        print(f"refreshed {update.refresh_skills(update.cache_dir())} skills")
    else:
        print(skills_dir())


def auto_update_flag(a: argparse.Namespace) -> bool:
    try:
        return load_config(resolve_root(a.dir) / CONFIG_NAME).auto_update
    except MiseError:
        return True  # a broken config is reported by the command itself


def start_auto_update(a: argparse.Namespace) -> None:
    """Once a day, in the background, never for send; the new version applies on the next run."""
    if a.cmd in NO_AUTO_UPDATE or not update.auto_enabled(os.environ, sys.stderr.isatty(), True):
        return
    if not auto_update_flag(a):
        return
    cache, now = update.cache_dir(), time.time()
    notice = update.pending_notice(cache, now, __version__, update.current_installer())
    if notice:
        print(notice, file=sys.stderr)
    update.start_background_check(cache, now)


def run(a: argparse.Namespace) -> None:
    if a.cmd == "demo":
        tour(a.demo_dir)
        return
    if a.cmd == "skills":
        run_skills(a)
        return
    if a.cmd == "update":
        print(
            update.update_now(update.cache_dir(), update.current_installer(), update.fetch_latest)
        )
        return
    store = Store(resolve_root(a.dir))
    if a.cmd in READ_CMDS:
        refresh_for_read(store)
    if a.cmd in SOURCE_CMDS:
        run_source_cmd(store, a)
    elif a.cmd == "reconcile":
        run_reconcile(store, a)
    elif a.cmd == "note-request":
        run_note_request(store, a)
    elif a.cmd == "requests":
        run_requests(store, a)
    elif a.cmd == "style":
        run_style(store, a)
    elif a.cmd == "lessons":
        run_lessons(store, a)
    else:
        run_journal(store, a)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        start_auto_update(args)
        run(args)
    except SourceError as e:
        print(f"miseenplace: {e}", file=sys.stderr)
        return 2
    except MiseError as e:
        print(f"miseenplace: {e}", file=sys.stderr)
        return 1
    return 0
