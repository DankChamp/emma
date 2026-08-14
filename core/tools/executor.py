"""
Tool executor — runs parsed directives against the AquaClient.

After Emma's AI responds, this module:
  1. Parses [TOOL:...] directives from the response
  2. Looks up each tool in the registry
  3. Executes the handler with parsed kwargs
  4. Returns a summary of what was executed (for logging/reporting)
"""
import logging

from core.tools.registry import get_registry
from core.tools.parser import parse_directives

logger = logging.getLogger("emma.tools")


async def execute_directives(text: str) -> list[dict]:
    registry = get_registry()
    directives = parse_directives(text)
    results = []

    for tool_name, kwargs in directives:
        tool = registry.get(tool_name)
        if tool is None:
            logger.warning("Unknown tool: %s", tool_name)
            results.append({"tool": tool_name, "status": "unknown", "error": f"No tool named '{tool_name}'"})
            continue

        try:
            handler = tool.handler
            result_text = await handler(**kwargs)
            results.append({"tool": tool_name, "kwargs": kwargs, "status": "ok", "result": result_text})
            logger.info("Tool %s executed: %s", tool_name, result_text[:100])
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc)
            results.append({"tool": tool_name, "kwargs": kwargs, "status": "error", "error": str(exc)})

    return results
