"""Credentials are capabilities: the recognizer library.

Whatever the chairman stores, the project can use — yaaf never polices what
somebody adds to their own deployment (docs/AUTONOMY.md). What the platform
owes in return is to make a stored credential *usable*: an env var in a
sandbox is only a capability if the agent holding it knows what it is for and
what tooling speaks it. A recognizer maps a credential's NAME to that
knowledge — a usage note and the packages worth installing (the network is
open; `pip install` is one Bash call away).

Recognition is a convenience, never a fence: an unrecognized credential still
injects exactly as before, described only by its name. The table matches on
substrings of the env-var name, first hit wins, and adding an entry is adding
a dict — deliberately boring so contributors extend it without ceremony.
"""

from __future__ import annotations

# Each rule: `match` substrings tried against the UPPERCASED env-var name
# (first rule with any hit wins), a one-line `note` the granted agent reads
# beside the variable, and `packages` worth `pip install`-ing before use
# (empty = the standard library already speaks it).
RECOGNIZERS: list[dict] = [
    {"match": ("SMTP", "POP3", "POP_", "IMAP", "MAILBOX"),
     "note": ("email credentials — send with smtplib, read with imaplib/poplib "
              "(all standard library); the paired host/user variables are usually "
              "stored alongside"),
     "packages": []},
    {"match": ("AWS_",),
     "note": "AWS — use boto3; region and paired keys are usually stored alongside",
     "packages": ["boto3"]},
    {"match": ("STRIPE",),
     "note": "Stripe API — use the stripe package; test keys start sk_test_",
     "packages": ["stripe"]},
    {"match": ("SENDGRID",),
     "note": "SendGrid — POST https://api.sendgrid.com/v3/mail/send with httpx",
     "packages": []},
    {"match": ("MAILGUN",),
     "note": "Mailgun — POST https://api.mailgun.net/v3/<domain>/messages with httpx",
     "packages": []},
    {"match": ("TWILIO",),
     "note": "Twilio — use the twilio package (SMS, voice, verify)",
     "packages": ["twilio"]},
    {"match": ("CLOUDFLARE", "CF_API"),
     "note": ("Cloudflare — httpx against https://api.cloudflare.com/client/v4 "
              "with 'Authorization: Bearer'; DNS records live under /zones"),
     "packages": []},
    {"match": ("NAMECHEAP",),
     "note": ("Namecheap registrar — XML API at "
              "https://api.namecheap.com/xml.response; domains.create registers "
              "a domain"),
     "packages": []},
    {"match": ("PORKBUN",),
     "note": "Porkbun registrar — JSON API at https://api.porkbun.com/api/json/v3",
     "packages": []},
    {"match": ("GODADDY",),
     "note": "GoDaddy — httpx against https://api.godaddy.com/v1 (sso-key auth)",
     "packages": []},
    {"match": ("GITHUB", "GH_TOKEN"),
     "note": ("GitHub — httpx against https://api.github.com with "
              "'Authorization: Bearer', or git over https with the token as the "
              "password"),
     "packages": []},
    {"match": ("SLACK",),
     "note": "Slack — httpx against https://slack.com/api (chat.postMessage etc.)",
     "packages": []},
    {"match": ("WORDPRESS", "WP_APP"),
     "note": ("WordPress — REST API at <site>/wp-json/wp/v2 with an application "
              "password (basic auth)"),
     "packages": []},
    {"match": ("OPENAI",),
     "note": "OpenAI API — use the openai package or httpx against api.openai.com",
     "packages": ["openai"]},
    {"match": ("ANTHROPIC",),
     "note": "Anthropic API — use the anthropic package",
     "packages": ["anthropic"]},
    {"match": ("POSTGRES", "DATABASE_URL", "MYSQL"),
     "note": "a database — psycopg for Postgres, pymysql for MySQL, or duckdb/pandas",
     "packages": ["psycopg[binary]"]},
    {"match": ("S3_", "MINIO"),
     "note": "S3-compatible storage — use boto3 with endpoint_url",
     "packages": ["boto3"]},
    {"match": ("CARD_", "CREDIT_CARD", "_CARD"),
     "note": ("a payment card the chairman stored — spend it only inside the "
              "constitution's envelope: clear with purchase_begin BEFORE paying, "
              "book with purchase_finish after"),
     "packages": []},
    {"match": ("TELEGRAM",),
     "note": "Telegram bot — httpx against https://api.telegram.org/bot<token>",
     "packages": []},
]


def recognize(env_name: str) -> dict | None:
    upper = env_name.upper()
    for rule in RECOGNIZERS:
        if any(fragment in upper for fragment in rule["match"]):
            return rule
    return None


def credentials_prompt(env_names: list[str]) -> str:
    """The CREDENTIALS paragraph for a run's system prompt: every granted
    variable, with what it is and how to use it where the platform knows.
    Returns "" when the role holds nothing."""
    if not env_names:
        return ""
    lines = ["\n\nCREDENTIALS: these environment variables are set in your sandbox "
             "for your role. Use them for their purpose; never print, log, or write "
             "them to files."]
    packages: list[str] = []
    for name in sorted(env_names):
        rule = recognize(name)
        if rule is None:
            lines.append(f"\n- {name}")
            continue
        lines.append(f"\n- {name}: {rule['note']}")
        packages += [p for p in rule["packages"] if p not in packages]
    if packages:
        lines.append(
            "\nPackages worth installing before first use (the network is open): "
            f"pip install {' '.join(packages)}"
        )
    return "".join(lines)
