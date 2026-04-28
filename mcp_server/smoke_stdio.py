"""End-to-end smoke test of the THAA MCP server over stdio.

Spawns ``python -m mcp_server.server`` as a subprocess (the same way
Claude Desktop does), goes through the MCP handshake, lists tools, and
invokes ``list_scenario_libraries`` to confirm the round trip works.

Run from the project root::

    python -m mcp_server.smoke_stdio
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        cwd=str(project_root),
    )

    print(f"[smoke] spawning server: {sys.executable} -m mcp_server.server")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"[smoke] initialize OK -- server={init.serverInfo.name} "
                  f"v{init.serverInfo.version}")

            tools_resp = await session.list_tools()
            tool_names = [t.name for t in tools_resp.tools]
            print(f"[smoke] tools/list ({len(tool_names)}): {tool_names}")
            expected = {
                "list_scenario_libraries",
                "run_verification",
                "start_hitl_run",
                "resume_hitl_run",
                "list_threads",
                "generate_pytest_from_tse",
            }
            missing = expected - set(tool_names)
            if missing:
                print(f"[smoke] FAIL -- missing tools: {missing}")
                return 1

            # Round-trip a real call. list_scenario_libraries is pure I/O,
            # no Anthropic API key required.
            call = await session.call_tool("list_scenario_libraries", {})
            if call.isError:
                print(f"[smoke] FAIL -- tool returned error: {call.content}")
                return 1

            # FastMCP encodes each list item as its own text block, so
            # decode every text block and treat them as one collection.
            blocks = [b for b in call.content if getattr(b, "type", "") == "text"]
            if not blocks:
                print("[smoke] FAIL -- no text content returned")
                return 1
            libraries = [json.loads(b.text) for b in blocks]
            print(f"[smoke] tools/call list_scenario_libraries -> "
                  f"{len(libraries)} libraries")
            for lib in libraries[:3]:
                print(f"          {lib['path']}: "
                      f"{lib['scenarios_count']} scenarios")
            if len(libraries) < 1 or "scenarios_count" not in libraries[0]:
                print("[smoke] FAIL -- unexpected payload shape")
                return 1
            print("[smoke] OK")
            return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
