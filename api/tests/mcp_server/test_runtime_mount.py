import anyio
import httpx
import sys

from api.mcp_server.server import mcp
from api import runtime_host


def test_http_ping_and_mcp_survive_console_import_failure(monkeypatch):
    original = runtime_host.console_app
    monkeypatch.setattr(runtime_host, "_console_import_error_logged", False)
    monkeypatch.setitem(sys.modules, "api.webui.server", None)
    app = runtime_host.create_host_app()

    async def exercise():
        async with mcp.session_manager.run():
            from mcp.client.session import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            http_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=app,
                    client=("127.0.0.1", 54321),
                ),
                base_url="http://127.0.0.1:8765",
                trust_env=False,
            )
            async with http_client:
                ping = await http_client.get("/api/runtime/ping")
                assert ping.status_code == 200
                assert ping.json()["ok"] is True
                async with streamable_http_client(
                    "http://127.0.0.1:8765/mcp",
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as client:
                        await client.initialize()
                        result = await client.list_tools()
                        names = {tool.name for tool in result.tools}
                        assert "list_courses" in names
                        assert "get_course_content" in names

    try:
        anyio.run(exercise)
    finally:
        runtime_host.console_app = original
