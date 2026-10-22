"""Merging two machines' audit logs: keep the remote's entries, re-append the local ones.

Two machines that both wrote since their last sync have each used the same next seq numbers
and possibly the same next item id. Git cannot merge that line by line, and renumbering only
`seq` would leave two different items with one id. So the local, unpushed entries are replayed
after the remote's: their seq continues the remote's, and an `add` whose id the remote already
used gets the next free id, with later local entries about that item following it. An update
whose recorded old value no longer matches (both machines changed the same field) is kept; the
next load reports it loudly and names the line, so no edit is lost silently.
"""

import copy

from miseenplace.model import Entry


def reappend(remote: list[Entry], local: list[Entry]) -> list[Entry]:
    """`remote` followed by `local` renumbered to continue it. Inputs are not modified."""
    seq = remote[-1]["seq"] if remote else 0
    used = {e["id"] for e in remote if e["op"] == "add"}
    moved: dict[int, int] = {}
    merged = [copy.deepcopy(e) for e in remote]
    for original in local:
        e = copy.deepcopy(original)
        seq += 1
        e["seq"] = seq
        if e["op"] == "add":
            new_id = e["id"] if e["id"] not in used else max(used) + 1
            used.add(new_id)
            moved[e["id"]] = new_id
            e["id"] = new_id
            e["changes"]["id"] = new_id
        elif e["op"] != "log":
            e["id"] = moved.get(e["id"], e["id"])
        merged.append(e)
    return merged
