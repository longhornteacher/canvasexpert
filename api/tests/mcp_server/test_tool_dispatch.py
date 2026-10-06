"""MCP dispatch responsiveness for slow synchronous tools."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest


@pytest.mark.parametrize("fails", [False, True])
def test_slow_discovery_does_not_block_other_event_loop_work(monkeypatch, fails):
    from mcp.server.fastmcp.exceptions import ToolError

    from api.mcp_server import server, tools

    started = threading.Event()
    release = threading.Event()
    unrelated_task_finished = threading.Event()
    observed_before_release: list[bool] = []
    expected_payload = {"ok": True, "marker": "synthetic"}

    def slow_discovery():
        started.set()
        if not release.wait(timeout=4):
            raise RuntimeError("test watchdog did not release discovery")
        if fails:
            raise ValueError("synthetic discovery failure")
        return expected_payload

    def watchdog():
        if started.wait(timeout=2):
            observed_before_release.append(unrelated_task_finished.wait(timeout=0.5))
        release.set()

    monkeypatch.setattr(tools, "discover_scoring_work", slow_discovery)
    watchdog_thread = threading.Thread(target=watchdog, daemon=True)
    watchdog_thread.start()

    async def call_tool_and_unrelated_task():
        async def unrelated_task():
            await asyncio.to_thread(started.wait, 2)
            unrelated_task_finished.set()

        unrelated = asyncio.create_task(unrelated_task())
        dispatch = asyncio.create_task(
            server.mcp.call_tool("discover_scoring_work", {})
        )
        try:
            if fails:
                with pytest.raises(ToolError, match="synthetic discovery failure"):
                    await dispatch
                result = None
            else:
                result = await dispatch
            await unrelated
            return result
        finally:
            release.set()
            if not dispatch.done():
                await dispatch
            await unrelated

    try:
        result = asyncio.run(call_tool_and_unrelated_task())
    finally:
        release.set()
        watchdog_thread.join(timeout=2)

    assert len(observed_before_release) == 1
    assert observed_before_release[0] is True
    if not fails:
        assert len(result) == 1
        assert result[0].text == json.dumps(expected_payload, separators=(",", ":"))
