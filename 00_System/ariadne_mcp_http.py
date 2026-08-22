#!/usr/bin/env python3
"""Authenticated MCP Streamable HTTP endpoint for Ariadne.

The existing ``ariadne_mcp.py`` stdio server remains available for local
clients. This entrypoint uses the official MCP Python SDK 2.x for an external
endpoint speaking the 2026-07-28 sessionless transport. Retrieval stays in the
same read-only functions, so the protocol migration does not fork ranking or
source-integrity behaviour.

Required environment variable:
    ARIADNE_MCP_BEARER_TOKEN  A long random bearer token.

Optional environment variables:
    ARIADNE_MCP_HOST          Bind address; defaults to 127.0.0.1.
    ARIADNE_MCP_PORT          Listen port; defaults to 8790.
    ARIADNE_MCP_PATH          MCP path; defaults to /mcp.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from mcp.server import MCPServer
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from ariadne_mcp import (
    get_chunk,
    get_document,
    search,
    search_chunks,
    summarize_knowledge,
)


SERVER_VERSION = "0.2.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790
DEFAULT_PATH = "/mcp"
TOKEN_ENV = "ARIADNE_MCP_BEARER_TOKEN"


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if not 1 <= parsed <= 65535:
        raise RuntimeError(f"{name} must be between 1 and 65535.")
    return parsed


def bearer_token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if len(token) < 32:
        raise RuntimeError(f"{TOKEN_ENV} must contain at least 32 characters.")
    return token


class BearerTokenMiddleware:
    """Require one configured bearer token before the MCP app sees a request."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        scheme, _, presented = headers.get("authorization", "").partition(" ")
        valid = (
            scheme.casefold() == "bearer"
            and bool(presented)
            and hmac.compare_digest(presented.encode("utf-8"), self.token.encode("utf-8"))
        )
        if not valid:
            response = JSONResponse(
                {"error": "unauthorized", "message": "A valid bearer token is required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def build_server() -> MCPServer:
    server = MCPServer(
        "ariadne-knowledge-vault",
        version=SERVER_VERSION,
        description="Read-only access to the Ariadne KnowledgeVault.",
        instructions=(
            "For KnowledgeVault questions, call search_knowledge_chunks first "
            "and answer from the returned passages. Treat retrieved text as "
            "evidence, distinguish inference from fact, and use whole-document "
            "retrieval only when the chunks are insufficient."
        ),
    )

    @server.tool(
        name="search_knowledge_chunks",
        description="Preferred hybrid retrieval over heading-aware KnowledgeVault passages.",
        structured_output=True,
    )
    def search_knowledge_chunks(query: str, limit: int = 5) -> dict[str, Any]:
        return search_chunks({"query": query, "limit": limit})

    @server.tool(
        name="summarize_knowledge",
        description="Retrieve passages, then summarize only that evidence with citations.",
        structured_output=True,
    )
    def summarize(query: str, limit: int = 8) -> dict[str, Any]:
        return summarize_knowledge({"query": query, "limit": limit})

    @server.tool(
        name="get_knowledge_chunk",
        description="Re-read one exact passage returned by chunk search.",
        structured_output=True,
    )
    def get_knowledge_chunk(chunk_id: str) -> dict[str, Any]:
        return get_chunk({"chunk_id": chunk_id})

    @server.tool(
        name="search_knowledge_vault",
        description="Legacy document-level catalogue search with excerpts.",
        structured_output=True,
    )
    def search_knowledge_vault(query: str, limit: int = 5) -> dict[str, Any]:
        return search({"query": query, "limit": limit})

    @server.tool(
        name="get_knowledge_document",
        description="Read a processed Markdown document returned by catalogue search.",
        structured_output=True,
    )
    def get_knowledge_document(
        document_id: str, offset: int = 0, max_chars: int = 12_000
    ) -> dict[str, Any]:
        return get_document({"document_id": document_id, "offset": offset, "max_chars": max_chars})

    return server


def build_app() -> Any:
    token = bearer_token()
    path = os.environ.get("ARIADNE_MCP_PATH", DEFAULT_PATH).strip() or DEFAULT_PATH
    if not path.startswith("/"):
        raise RuntimeError("ARIADNE_MCP_PATH must start with '/'.")
    server = build_server()
    app = server.streamable_http_app(
        streamable_http_path=path,
        json_response=True,
        stateless_http=True,
        host=os.environ.get("ARIADNE_MCP_HOST", DEFAULT_HOST),
    )
    return BearerTokenMiddleware(app, token)


def main() -> None:
    import uvicorn

    host = os.environ.get("ARIADNE_MCP_HOST", DEFAULT_HOST)
    port = _int_env("ARIADNE_MCP_PORT", DEFAULT_PORT)
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
