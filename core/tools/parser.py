"""
Action directive parser.

Parses [TOOL:name key=value ...] directives from AI response text.
This is the same pattern as Emma's Telegram [ACTION:...] system, but
generalized to any tool.

Directive format:
  [TOOL:tool_name key1="value1" key2=value2 key3="multi word value"]

Values can be:
  - Quoted strings: "hello world"
  - Bare words: hello (no spaces)
  - Numbers: 42 or 3.14
  - Booleans: true, false
  - Lists: [item1, item2, item3]

Returns a list of (tool_name, kwargs) tuples.
"""
import re
import shlex
from typing import Any

TOOL_RE = re.compile(r"\[TOOL:(\w+)([^\]]*)\]")


def parse_directives(text: str) -> list[tuple[str, dict[str, Any]]]:
    results = []
    for match in TOOL_RE.finditer(text):
        tool_name = match.group(1)
        args_str = match.group(2).strip()
        kwargs = _parse_args(args_str)
        results.append((tool_name, kwargs))
    return results


def _parse_args(args_str: str) -> dict[str, Any]:
    if not args_str:
        return {}
    kwargs = {}
    try:
        tokens = shlex.split(args_str)
    except ValueError:
        tokens = args_str.split()

    for token in tokens:
        if "=" in token:
            key, _, value = token.partition("=")
            kwargs[key] = _parse_value(value)
    return kwargs


def _parse_value(value: str) -> Any:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if inner:
            return [item.strip().strip('"') for item in inner.split(",")]
        return []
    return value


def strip_directives(text: str) -> str:
    return TOOL_RE.sub("", text).strip()
