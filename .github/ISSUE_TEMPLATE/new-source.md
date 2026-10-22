---
name: New source
about: Propose or announce a chat source (Slack, GitHub, Gmail, ...)
labels: source
---

**Service**:

**Who would use it?** (me / my team / many)

**Can it do these five things through an API?** Check each.

- [ ] list messages newer than a position, addressed to me, excluding mine (`new`)
- [ ] read one thread or conversation (`thread`)
- [ ] post a message to a thread or person (`send`, only ever from a stored draft)
- [ ] find a person by name (`users`)
- [ ] a personal credential I can create (not an admin-only bot)

**I will write it**: yes / no. See `docs/writing-a-source.md`; a source is one module plus tests.
