"""yaaf as an MCP server — the chairman's surface, for a model client.

The mirror of `yaaf.server.mcp_oauth`, which connects yaaf *to* other people's
MCP servers as data sources for agents. This package is the other direction:
reading and steering a company from Claude Code, Claude Desktop, or anything
else that speaks the protocol.

Every tool is a projection of a route that already exists in
`yaaf.server.api.observe` or `admin`. No rule lives here and nowhere else —
if a tool would need behavior the HTTP surface lacks, the HTTP surface gets it
first. See docs/MCP_SERVER.md for the design and the milestones.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from yaaf.server.mcp import protocol, tools_read  # noqa: F401 - registers the tools
from yaaf.server.mcp.auth import Principal, mcp_auth, mcp_db

router = APIRouter()


@router.post("")
@router.post("/")
async def rpc(
    request: Request,
    principal: Principal = Depends(mcp_auth),
    session: Session = Depends(mcp_db),
) -> Response:
    try:
        message = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "jsonrpc": "2.0", "id": None,
            "error": {"code": protocol.PARSE_ERROR, "message": "invalid JSON"},
        })

    # A batch is a list. Notifications inside it produce nothing, and a batch
    # that is all notifications gets 202 like a single one would.
    if isinstance(message, list):
        answers = [a for a in (protocol.handle(m, principal, session) for m in message)
                   if a is not None]
        return Response(status_code=202) if not answers else JSONResponse(content=answers)

    answer = protocol.handle(message, principal, session)
    return Response(status_code=202) if answer is None else JSONResponse(content=answer)


@router.get("")
@router.get("/")
def no_stream(principal: Principal = Depends(mcp_auth)) -> Response:
    """No SSE stream: nothing on this surface is server-initiated.

    Authenticated before it answers, so an unauthenticated probe cannot use the
    405 to learn that the endpoint is here.
    """
    return JSONResponse(status_code=405, content={
        "jsonrpc": "2.0", "id": None,
        "error": {"code": protocol.METHOD_NOT_FOUND,
                  "message": "this server does not open SSE streams; POST JSON-RPC instead"},
    })
