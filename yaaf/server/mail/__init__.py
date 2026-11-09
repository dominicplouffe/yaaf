"""The project mailbox: the CEO's correspondence with the world outside.

A project can be given an email address of its own — its own SMTP server to
send from, its own IMAP server to read — so the CEO can send a finished
deliverable to somebody and hear back. The somebody may be a person or another
agent on any platform; to yaaf they are a *correspondent*, and nothing they
write ever carries the chairman's authority.

The platform's own SMTP settings (`YAAF_SMTP_*`) are not involved and never a
fallback: they carry the platform's alarms to the chairman, and a project email
sent from them would come from an address nobody reads replies on.

Modules:

- :mod:`.settings` — the chairman's configuration and its validation
- :mod:`.wire` — SMTP and IMAP, module-level so tests replace the network only
- :mod:`.inbound` — what a fetched email must prove before the CEO hears of it
- :mod:`.service` — the CEO's verbs and the loop that sends and reads
"""
