"""Public low-level MCP Server wiring; scope is loaded once at startup."""

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from cli.shared.scope import load_scope

from . import __version__
from .handlers import Handlers
from .transport import Outbound, StrictInput, TrackedInput


def create_server(handlers, output):
    server = Server("windoctor", version=__version__,
                    on_list_tools=handlers.list_tools, on_call_tool=handlers.call_tool)

    async def observe(ctx, call_next):
        try:
            return await call_next(ctx)
        except anyio.get_cancelled_exc_class():
            if ctx.request_id is not None:
                output.cancel(ctx.request_id)
            raise
        except (MCPError, ValidationError):
            raise
        except Exception:
            raise MCPError(-32603, "Internal error") from None

    server.middleware.append(observe)
    return server


async def serve(scope, fds):
    output = Outbound(fds[1], fds[2])
    server = create_server(Handlers(scope), output)
    try:
        async with anyio.create_task_group() as group:
            output.abort = group.cancel_scope.cancel
            group.start_soon(output.pump)
            async with stdio_server(stdin=StrictInput(fds[0], output), stdout=output) as (read_stream, write_stream):
                await server.run(TrackedInput(read_stream, output), write_stream,
                                 server.create_initialization_options())
            group.cancel_scope.cancel()
    finally:
        output.close()


async def run(scope_path, fds):
    scope = load_scope(scope_path)
    await serve(scope, fds)
