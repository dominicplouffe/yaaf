"""Give the demo install (tests/e2e/demo.py) the Settings a real install has,
through the same admin API the page calls. Not product code.

    python tests/e2e/demo.py /tmp/yaaf-demo --serve=8765      # in one shell
    python docs/ux-redesign/settings/seed/configure.py         # then this

Every key below is a labelled sample that no provider would accept. That is
deliberate: it is how the study found that a key nobody has seen answer is
still drawn as green "Ready".
"""
import os

import requests

BASE = os.environ.get("YAAF_URL", "http://localhost:8765")
s = requests.Session()
# The server refuses a browser write that does not come from its own pages.
s.headers.update({"Origin": BASE, "Referer": BASE + "/app/"})
s.post(f"{BASE}/auth/login", json={"email": "dominic@example.com",
                                   "password": "demo-password-123"}).raise_for_status()


def call(method, path, body=None):
    r = s.request(method, BASE + path, json=body)
    print(method, path, r.status_code)
    r.raise_for_status()
    return r.json() if r.content else None


keys = {}
for name, provider, value, extra in [
    ("Anthropic — team", "anthropic", "sample-not-a-real-key-1", {}),
    ("OpenAI — research", "openai", "sample-not-a-real-key-2", {}),
    ("DeepSeek", "deepseek", "sample-not-a-real-key-3", {}),
    ("Ollama on the studio Mac", "ollama", "", {"base_url": "http://studio.local:11434"}),
    ("Old OpenAI trial", "openai", "sample-not-a-real-key-4", {}),
]:
    keys[name] = call("POST", "/admin/settings/llm-credentials",
                      {"name": name, "provider": provider, "mode": "api_key", "value": value, **extra})["id"]

call("POST", "/admin/settings/llm-credentials/hide", {"ids": [keys["Old OpenAI trial"]]})
call("PUT", "/admin/founder/credential/llm", {"credential_id": keys["Anthropic — team"]})
call("PUT", "/admin/settings/slack", {
    "bot_token": "xoxb-sample-not-a-real-token", "signing_secret": "sample-not-a-real-secret",
    "chairman_slack_user": "U0SAMPLE01", "channel_prefix": "", "channel_visibility": "private",
    "auto_create_channels": True})
call("PUT", "/admin/settings/web-search", {"backend": "brave", "key": "sample-not-a-real-key-5",
                                           "base_url": None, "price_usd": 0.005})
for email, name, admin in [("ines@example.com", "Ines Laurent", False),
                           ("sam@example.com", "Sam Okafor", True),
                           ("priya@example.com", "Priya Raman", False)]:
    call("POST", "/admin/users", {"email": email, "display_name": name, "is_admin": admin})
for name, days in [("laptop CLI", 30), ("nightly-export on the build box", 90)]:
    call("POST", "/auth/tokens", {"name": name, "days": days})
call("POST", "/admin/settings/model-prices/import",
     {"source": "snapshot", "apply": True, "provider": "deepseek"})
call("PUT", "/admin/settings/models/ceiling", {"provider": "anthropic", "limit": 6})

# Four projects pay with three of the keys, so "in use" and "pays …" appear.
projects = s.get(f"{BASE}/observe/projects").json()
for project, (provider, key) in zip(projects[:4], [("anthropic", "Anthropic — team"),
                                                   ("anthropic", "Anthropic — team"),
                                                   ("deepseek", "DeepSeek"),
                                                   ("openai", "OpenAI — research")], strict=True):
    call("PUT", f"/admin/projects/{project['id']}/credentials/{provider}", {"credential_id": keys[key]})

# This script's own sign-in would show up under Account → Where you are signed in.
for row in sorted(s.get(f"{BASE}/auth/sessions").json(), key=lambda r: r.get("current", False)):
    if "python-requests" in (row.get("user_agent") or ""):
        s.delete(f"{BASE}/auth/sessions/{row['id']}")
