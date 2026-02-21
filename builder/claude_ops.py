"""
claude_ops.py — Claude API integration for streaming code generation.
"""
import os
import re
from pathlib import Path
from typing import Callable, Generator

import anthropic

MODEL = "claude-opus-4-6"
PROMPTS_DIR = Path(__file__).parent / "claude_prompts"


def _load_system_prompt(template: str, app_name: str) -> str:
    prompt_file = PROMPTS_DIR / f"{template}.md"
    if not prompt_file.exists():
        raise ValueError(f"No system prompt for template: {template}")
    text = prompt_file.read_text()
    return text.replace("{{APP_NAME}}", app_name)


def _extract_files(text: str) -> dict[str, str]:
    """Extract <file name="...">...</file> blocks from Claude's response."""
    pattern = r'<file name="([^"]+)">\n(.*?)\n</file>'
    matches = re.findall(pattern, text, re.DOTALL)
    return {name: content for name, content in matches}


def generate_code_stream(
    app_name: str,
    template: str,
    description: str,
    current_files: dict[str, str] | None = None,
) -> Generator[str, None, None]:
    """
    Stream raw text chunks from Claude. Yields text chunks as they arrive.
    The caller accumulates the full response to extract files.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    system = _load_system_prompt(template, app_name)

    existing_block = ""
    if current_files:
        existing_block = "\n".join(
            f'<existing file="{k}">\n{v}\n</existing>'
            for k, v in current_files.items()
        )

    user_message = description
    if existing_block:
        user_message = f"{description}\n\nExisting files:\n{existing_block}"

    with client.messages.stream(
        model=MODEL,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for chunk in stream.text_stream:
            yield chunk


def generate_code(
    app_name: str,
    template: str,
    description: str,
    current_files: dict[str, str] | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> dict:
    """
    Non-streaming wrapper. Returns {"files": {filename: content, ...}}.
    Optionally calls on_chunk(text) for each streamed chunk.
    """
    full_text = ""
    for chunk in generate_code_stream(app_name, template, description, current_files):
        full_text += chunk
        if on_chunk:
            on_chunk(chunk)

    files = _extract_files(full_text)
    return {"files": files, "raw": full_text}
