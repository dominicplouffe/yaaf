"""The redirects into the web UI must not pick a scheme.

yaaf usually sits behind a TLS-terminating proxy, so the scheme uvicorn sees is
http while the browser's is https. An absolute redirect built from the request
sends the browser through a plain-http hop on an https site, which browsers
flag as not secure. A relative Location keeps the browser's own scheme.
"""

from __future__ import annotations

import pytest

from yaaf.server.app import _web_dist

pytestmark = pytest.mark.skipif(_web_dist() is None, reason="web UI not built (web/dist missing)")


@pytest.mark.parametrize("path", ["/app", "/"])
def test_redirect_into_the_ui_is_relative(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code in (307, 308)
    assert response.headers["location"] == "/app/"


def test_the_ui_shell_itself_is_served(client):
    response = client.get("/app/")
    assert response.status_code == 200
    assert "<div id=\"root\">" in response.text
