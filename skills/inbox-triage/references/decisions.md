# Triage decisions

How `mep inbox` picks the suggested action, so you know when to override it.

- A direct ask (`can you`, `please`, a question mark addressed to the user) is `reply`, or `todo` when it contains a work verb (review, write, fix, send, update, check).
- A promise from someone else (`I'll send`, `I will check`, `working on it`) is `waiting`.
- Chatter, thanks and acknowledgements are `ignore`.
- A message the user already answered later in the same thread is hidden.
- Messages from noise channels (config `noise_streams`) show only when they mention the user.

Override when: the ask is really for someone else in the thread (ignore), the user told you earlier that the topic is parked (ignore), or a "reply" ask is a work request in disguise (todo).

Dates: for `todo` the due date, for `waiting` the day to nudge. When the message gives no date, omit `--eta`.
