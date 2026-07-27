"""
Tool registry — defines what tools Aqua (and the action system) can execute.

Each tool has:
  - name: unique identifier used in [TOOL:name] directives
  - description: what it does (injected into system prompt)
  - params: list of (name, description, required) tuples
  - handler: async callable that takes kwargs and returns a string result

Tools are registered once at startup and dispatched by the action parser
after every AI response.
"""
from typing import Any, Callable, Coroutine

Handler = Callable[..., Coroutine[Any, Any, str]]


class Tool:
    def __init__(self, name: str, description: str, params: list[tuple[str, str, bool]], handler: Handler):
        self.name = name
        self.description = description
        self.params = params
        self.handler = handler

    def signature(self) -> str:
        """Human-readable signature for system prompts."""
        parts = [f"  {name}: {desc}" for name, desc, _ in self.params]
        req = [name for name, _, r in self.params if r]
        opt = [name for name, _, r in self.params if not r]
        req_str = f" ({', '.join(req)})" if req else ""
        opt_str = f" optional: ({', '.join(opt)})" if opt else ""
        return (
            f"[TOOL:{self.name}]"
            f"{req_str}{opt_str}\n  {self.description}\n"
            + "\n".join(parts)
        )


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def system_prompt_block(self) -> str:
        if not self._tools:
            return ""
        lines = [
            "You can use tools by appending directives to your response.",
            "Each directive goes on its own line in the format: [TOOL:name key=value ...]",
            "Available tools:",
        ]
        for tool in self._tools.values():
            lines.append("")
            lines.append(tool.signature())
        lines.append("")
        lines.append("You may use multiple tools in a single response.")
        return "\n".join(lines)


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
