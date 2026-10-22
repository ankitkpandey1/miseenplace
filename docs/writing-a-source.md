---
description: A 30-minute guide to adding a chat or ticket source to miseenplace - the contract, a template, tests and registration.
---

# Writing a source (30 minutes)

Budget: 5 minutes to read, 15 to implement, 10 to test. A source is a class; no framework.

## 1. Read the reference (5 min)

`src/miseenplace/sources/demo.py` is the smallest complete source. `src/miseenplace/sources/base.py` has the Protocol.

## 2. Copy the template (1 min)

```bash
cp src/miseenplace/sources/_template.py src/miseenplace/sources/myservice.py
```

## 3. Implement (15 min)

- `new(cursor, init)`: call `consume(self.cursors, cursor, init, newest, after)`. It sets a baseline for an unknown cursor and advances the position only after you return. Return messages **not sent by the user**.
- `thread(target)`: the conversation, with `mine=True` on the user's own messages.
- `draft(target, text)`: `return self.outbox.draft(target, text)`. If the draft quotes something, put the quote in the text now, so the stored text is exactly what is posted.
- `send(draft_id)`: `return self.outbox.send(draft_id)`, where the Outbox calls your `_deliver(target, text)` once.
- `users(query)`: return `Person` rows.
- Map every network or auth failure to `SourceError("... ; will retry")`. Never print a credential.
- Fill `Message.target` so a reply can be addressed.

## 4. Register (1 min)

Add the class to `REGISTRY` in `sources/__init__.py` and a `from_config(root, settings)` classmethod reading `[sources.myservice]`.

## 5. Test (10 min)

Add your source to `tests/contract/test_source_contract.py`. The contract tests are read-only and skip without credentials (set an environment variable pattern like the Zulip case). No mocks: use a real account or a real local server.

## 6. Ship

Open a PR with the docs line in [Sources](sources.md). Use the `new-source` issue template to discuss first.
