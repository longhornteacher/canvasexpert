import anyio
import httpx

from api.mcp_server.server import mcp
from api.webui.server import app


def test_lock_owner_exposes_the_same_tool_list_on_loopback():
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
                async with streamable_http_client(
                    "http://127.0.0.1:8765/mcp",
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as client:
                        await client.initialize()
                        result = await client.list_tools()
                        names = {tool.name for tool in result.tools}
                        assert "list_courses" in names
                        assert "get_course_assignments" in names

    anyio.run(exercise)
