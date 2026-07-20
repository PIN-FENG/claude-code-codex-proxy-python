#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Python port based on https://github.com/raine/claude-code-proxy
# See LICENSE and NOTICE for copyright, license terms, and attribution.
"""Run a local Anthropic-compatible proxy backed by Codex OAuth or an API key.

This single-file implementation requires Python 3.11 or newer and uses only
the Python standard library. It listens on 127.0.0.1, reads Codex configuration
and authentication without modifying them, and sends requests only to exact
OpenAI HTTPS endpoints allowlisted by this project.

The upstream model is selected by CODEX_PROXY_MODEL or ~/.codex/config.toml,
not by the model name sent by Claude Code. See README.md for the complete setup,
security notes, privacy disclosure, and troubleshooting guide.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]


VERSION = "0.1.0"
DEFAULT_PORT = 18888
MAX_REQUEST_BYTES = 32 * 1024 * 1024
UPSTREAM_TIMEOUT_SECONDS = 600
CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ALLOWED_UPSTREAM_ENDPOINTS = frozenset({CHATGPT_RESPONSES_URL, OPENAI_RESPONSES_URL})


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: BinaryIO, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise HTTPError(req.full_url, code, "Upstream redirect rejected", headers, fp)


UPSTREAM_OPENER = build_opener(RejectRedirects())


def debug(message: str) -> None:
    """Write metadata-only diagnostics; never pass prompts or credentials."""
    if os.environ.get("CODEX_PROXY_DEBUG") == "1":
        now = time.strftime("%H:%M:%S")
        print(f"[{now}] DEBUG {message}", file=sys.stderr, flush=True)


class ProxyError(Exception):
    """An error safe to show to the local caller."""

    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class CodexSettings:
    codex_home: Path
    model: str
    reasoning_effort: str | None


@dataclass(frozen=True)
class CodexAuth:
    mode: str
    token: str
    account_id: str | None
    endpoint: str


def codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    return Path(override).expanduser() if override else Path.home() / ".codex"


def read_settings() -> CodexSettings:
    home = codex_home()
    path = home / "config.toml"
    config: dict[str, Any] = {}
    if path.exists():
        if tomllib is None:
            raise ProxyError("Python 3.11 or newer is required to read config.toml")
        try:
            with path.open("rb") as handle:
                config = tomllib.load(handle)
        except (OSError, ValueError) as exc:
            raise ProxyError(f"Cannot read Codex config: {path}: {exc}") from exc

    profile_name = os.environ.get("CODEX_PROFILE") or _string(config.get("profile"))
    profile: dict[str, Any] = {}
    profiles = config.get("profiles")
    if profile_name and isinstance(profiles, dict):
        candidate = profiles.get(profile_name)
        if isinstance(candidate, dict):
            profile = candidate

    model = (
        os.environ.get("CODEX_PROXY_MODEL")
        or _string(profile.get("model"))
        or _string(config.get("model"))
    )
    if not model:
        raise ProxyError(
            f"No model is configured in {path}. Set model = \"...\" there or set "
            "CODEX_PROXY_MODEL."
        )

    effort = (
        os.environ.get("CODEX_PROXY_REASONING_EFFORT")
        or _string(profile.get("model_reasoning_effort"))
        or _string(config.get("model_reasoning_effort"))
    )
    if effort and effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
        raise ProxyError(f"Unsupported Codex reasoning effort: {effort}")
    return CodexSettings(home, model, effort)


def read_auth(settings: CodexSettings) -> CodexAuth:
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        return CodexAuth("api_key", env_key, None, OPENAI_RESPONSES_URL)

    path = settings.codex_home / "auth.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProxyError(f"Codex authentication was not found: {path}. Run: codex login", 401) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProxyError(f"Cannot read Codex authentication: {path}: {exc}", 401) from exc
    if not isinstance(document, dict):
        raise ProxyError(f"Unexpected Codex authentication format: {path}", 401)

    api_key = _string(document.get("OPENAI_API_KEY"))
    if api_key:
        return CodexAuth("api_key", api_key, None, OPENAI_RESPONSES_URL)

    tokens = document.get("tokens")
    token_map = tokens if isinstance(tokens, dict) else document
    access = _first_string(token_map, "access_token", "access", "token")
    if not access:
        raise ProxyError(f"No usable access token in {path}. Run: codex login", 401)

    account_id = _first_string(
        token_map, "account_id", "chatgpt_account_id", "chatgptAccountId"
    ) or _jwt_account_id(access)
    if not account_id:
        id_token = _first_string(token_map, "id_token", "id")
        if id_token:
            account_id = _jwt_account_id(id_token)
    if not account_id:
        raise ProxyError(
            "Codex OAuth is present, but its ChatGPT account ID could not be read. "
            "Run: codex login",
            401,
        )
    return CodexAuth("chatgpt", access, account_id, CHATGPT_RESPONSES_URL)


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _first_string(values: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _string(values.get(key))
        if value:
            return value
    return None


def _jwt_payload(token: str) -> dict[str, Any]:
    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _jwt_account_id(token: str) -> str | None:
    payload = _jwt_payload(token)
    direct = _first_string(payload, "chatgpt_account_id", "account_id")
    if direct:
        return direct
    auth_claim = payload.get("https://api.openai.com/auth")
    if isinstance(auth_claim, dict):
        direct = _first_string(auth_claim, "chatgpt_account_id", "account_id")
        if direct:
            return direct
    return _string(payload.get("https://api.openai.com/auth.chatgpt_account_id"))


def flatten_system(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if not isinstance(value, list):
        return None
    text = [block.get("text", "") for block in value if isinstance(block, dict) and block.get("type") == "text"]
    joined = "\n\n".join(part for part in text if isinstance(part, str) and part)
    return joined or None


def normalize_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def image_url(source: Any) -> str | None:
    if not isinstance(source, dict):
        return None
    if source.get("type") == "url":
        return _string(source.get("url"))
    if source.get("type") == "base64":
        media = _string(source.get("media_type")) or "application/octet-stream"
        data = _string(source.get("data"))
        return f"data:{media};base64,{data}" if data else None
    return None


def tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind == "image":
            parts.append("[image omitted from tool result]")
        else:
            parts.append(f"[unsupported tool-result block omitted: {kind or 'unknown'}]")
    return "\n".join(parts)


def translate_tools(tools: Any) -> list[dict[str, Any]] | None:
    if not isinstance(tools, list):
        return None
    result: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        # Claude's hosted web-search protocol has different result blocks. The
        # normal Claude Code WebSearch function is translated like every other
        # function; hosted declarations are intentionally omitted here.
        if tool.get("type") == "web_search_20250305":
            continue
        name = _string(tool.get("name"))
        if not name:
            continue
        schema = tool.get("input_schema")
        result.append(
            {
                "type": "function",
                "name": name,
                "description": _string(tool.get("description")) or "",
                "parameters": schema if isinstance(schema, dict) else {"type": "object"},
                "strict": False,
            }
        )
    return result or None


def translate_tool_choice(choice: Any, available_tools: list[dict[str, Any]] | None) -> Any:
    if isinstance(choice, str):
        return {"any": "required", "required": "required"}.get(choice, choice)
    if not isinstance(choice, dict):
        return None
    kind = choice.get("type", "auto")
    if kind in {"any", "required"}:
        return "required"
    if kind in {"auto", "none"}:
        return kind
    if kind == "tool" and _string(choice.get("name")):
        name = choice["name"]
        if available_tools and any(tool.get("name") == name for tool in available_tools):
            return {"type": "function", "name": name}
    return "auto"


def build_input(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        raise ProxyError("messages must be an array", 400)
    result: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        blocks = normalize_blocks(message.get("content"))
        if role == "user":
            parts: list[dict[str, Any]] = []
            for block in blocks:
                kind = block.get("type")
                if kind == "text" and isinstance(block.get("text"), str):
                    parts.append({"type": "input_text", "text": block["text"]})
                elif kind == "image":
                    url = image_url(block.get("source"))
                    if url:
                        parts.append({"type": "input_image", "image_url": url})
                elif kind == "tool_result":
                    if parts:
                        result.append({"type": "message", "role": "user", "content": parts})
                        parts = []
                    output = tool_result_text(block.get("content"))
                    if block.get("is_error"):
                        output = "[tool execution error]\n" + output
                    result.append(
                        {
                            "type": "function_call_output",
                            "call_id": str(block.get("tool_use_id", "")),
                            "output": output,
                        }
                    )
            if parts:
                result.append({"type": "message", "role": "user", "content": parts})
        else:
            parts = []
            for block in blocks:
                kind = block.get("type")
                if kind == "text" and isinstance(block.get("text"), str):
                    parts.append({"type": "output_text", "text": block["text"]})
                elif kind == "tool_use":
                    if parts:
                        result.append({"type": "message", "role": "assistant", "content": parts})
                        parts = []
                    tool_input = block.get("input")
                    result.append(
                        {
                            "type": "function_call",
                            "call_id": str(block.get("id", "")),
                            "name": str(block.get("name", "")),
                            "arguments": json.dumps(
                                tool_input if isinstance(tool_input, dict) else {},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )
            if parts:
                result.append({"type": "message", "role": "assistant", "content": parts})
    return result


def translate_request(body: dict[str, Any], settings: CodexSettings) -> dict[str, Any]:
    tools = translate_tools(body.get("tools"))
    lite = uses_responses_lite(settings.model)
    request: dict[str, Any] = {
        "model": settings.model,
        "input": build_input(body.get("messages")),
        "store": False,
        "stream": True,
        "parallel_tool_calls": not lite,
        "text": {"verbosity": "low"},
    }
    instructions = flatten_system(body.get("system"))
    if lite:
        # GPT-5.6 subscription models use Codex's Responses Lite lane. Lite
        # receives tools and developer instructions as leading input items.
        prefix: list[dict[str, Any]] = []
        if tools:
            prefix.append(
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": tools,
                }
            )
        if instructions:
            prefix.append(
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": instructions}],
                }
            )
        request["input"] = prefix + request["input"]
        request["client_metadata"] = {
            "ws_request_header_x_openai_internal_codex_responses_lite": "true"
        }
    else:
        if instructions:
            request["instructions"] = instructions
        if tools:
            request["tools"] = tools
    tool_choice = translate_tool_choice(body.get("tool_choice"), tools)
    if tool_choice is not None:
        request["tool_choice"] = tool_choice
    if settings.reasoning_effort and settings.reasoning_effort != "none":
        request["reasoning"] = {"effort": settings.reasoning_effort}
        if lite:
            request["reasoning"]["context"] = "all_turns"
    elif lite:
        request["reasoning"] = {"context": "all_turns"}
    session = _string(body.get("metadata", {}).get("user_id")) if isinstance(body.get("metadata"), dict) else None
    if session:
        request["prompt_cache_key"] = session
    return request


def uses_responses_lite(model: str) -> bool:
    return model in {"gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"}


def upstream_headers(
    auth: CodexAuth, session_id: str | None, responses_lite: bool
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {auth.token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": f"codex_proxy/{VERSION}",
    }
    if auth.mode == "chatgpt":
        headers.update(
            {
                "ChatGPT-Account-Id": auth.account_id or "",
                "OpenAI-Beta": "responses=experimental",
                "originator": "codex_cli_rs" if responses_lite else "codex_proxy",
            }
        )
        if responses_lite:
            headers["x-openai-internal-codex-responses-lite"] = "true"
            headers["User-Agent"] = "codex_cli_rs"
    if session_id:
        headers["session_id"] = session_id
        headers["x-client-request-id"] = str(uuid.uuid4())
    return headers


def validate_upstream_endpoint(endpoint: str) -> None:
    if endpoint not in ALLOWED_UPSTREAM_ENDPOINTS:
        raise ProxyError("Refusing to send credentials to an unapproved upstream endpoint", 502)


def open_upstream(
    request_body: dict[str, Any], auth: CodexAuth, session_id: str | None
) -> BinaryIO:
    validate_upstream_endpoint(auth.endpoint)
    model = str(request_body.get("model", ""))
    lite = uses_responses_lite(model)
    debug(
        f"upstream request endpoint={auth.endpoint} auth={auth.mode} "
        f"model={model} responses_lite={lite}"
    )
    encoded = json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        auth.endpoint,
        data=encoded,
        headers=upstream_headers(
            auth,
            session_id,
            uses_responses_lite(str(request_body.get("model", ""))),
        ),
        method="POST",
    )
    try:
        response = UPSTREAM_OPENER.open(request, timeout=UPSTREAM_TIMEOUT_SECONDS)
        debug(
            f"upstream connected status={getattr(response, 'status', 'unknown')} "
            f"content_type={response.headers.get('content-type', 'unknown')}"
        )
        return response
    except HTTPError as exc:
        debug(f"upstream HTTP error status={exc.code}")
        try:
            exc.close()
        except OSError:
            pass
        status = 502 if 300 <= exc.code < 400 else exc.code
        raise ProxyError(upstream_http_error_message(exc.code), status) from exc
    except URLError as exc:
        debug(f"upstream connection error type={type(exc.reason).__name__}")
        raise ProxyError("Cannot connect to OpenAI", 502) from exc


def upstream_http_error_message(status: int) -> str:
    if 300 <= status < 400:
        return "OpenAI redirected the request; redirect was rejected for security"
    if status == 401:
        return "Codex authentication expired; run: codex login"
    return f"OpenAI returned HTTP {status}"


def iter_sse(stream: BinaryIO) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    for raw in stream:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                data_lines.clear()
                if data != "[DONE]":
                    try:
                        value = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        yield value
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            value = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return
        if isinstance(value, dict):
            yield value


class AnthropicTranslator:
    def __init__(self, model: str):
        self.model = model
        self.message_id = "msg_" + uuid.uuid4().hex
        self.started = False
        self.finished = False
        self.next_index = 0
        self.blocks: dict[int, dict[str, Any]] = {}
        self.item_to_output: dict[str, int] = {}
        self.content: list[dict[str, Any]] = []
        self.saw_tool = False
        self.stop_reason: str | None = None
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    def _message_start(self) -> list[tuple[str, dict[str, Any]]]:
        if self.started:
            return []
        self.started = True
        return [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": self.message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": self.model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
        ]

    def _output_index(self, event: dict[str, Any]) -> int | None:
        value = event.get("output_index")
        if isinstance(value, int):
            return value
        item_id = _string(event.get("item_id"))
        return self.item_to_output.get(item_id) if item_id else None

    def _start_item(self, output_index: int, item: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        kind = item.get("type")
        if kind not in {"message", "function_call"} or output_index in self.blocks:
            return []
        index = self.next_index
        self.next_index += 1
        item_id = _string(item.get("id"))
        if item_id:
            self.item_to_output[item_id] = output_index
        events = self._message_start()
        if kind == "message":
            block = {"kind": "text", "index": index, "text": "", "closed": False}
            self.content.append({"type": "text", "text": ""})
            block["content_index"] = len(self.content) - 1
            events.append(
                (
                    "content_block_start",
                    {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
                )
            )
        else:
            call_id = str(item.get("call_id") or item.get("id") or ("call_" + uuid.uuid4().hex))
            name = str(item.get("name") or "")
            block = {
                "kind": "tool",
                "index": index,
                "id": call_id,
                "name": name,
                "arguments": "",
                "closed": False,
            }
            self.content.append({"type": "tool_use", "id": call_id, "name": name, "input": {}})
            block["content_index"] = len(self.content) - 1
            self.saw_tool = True
            events.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
                    },
                )
            )
        self.blocks[output_index] = block
        return events

    def _close(self, output_index: int, item: dict[str, Any] | None = None) -> list[tuple[str, dict[str, Any]]]:
        block = self.blocks.get(output_index)
        if not block or block["closed"]:
            return []
        events: list[tuple[str, dict[str, Any]]] = []
        if block["kind"] == "text" and item:
            full_text = "".join(
                str(part.get("text", ""))
                for part in item.get("content", [])
                if isinstance(part, dict) and part.get("type") == "output_text"
            )
            if full_text and not block["text"]:
                block["text"] = full_text
                self.content[block["content_index"]]["text"] = full_text
                events.append(
                    (
                        "content_block_delta",
                        {"type": "content_block_delta", "index": block["index"], "delta": {"type": "text_delta", "text": full_text}},
                    )
                )
        elif block["kind"] == "tool":
            final_args = _string(item.get("arguments")) if item else None
            if final_args and not block["arguments"]:
                block["arguments"] = final_args
                events.append(
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": block["index"],
                            "delta": {"type": "input_json_delta", "partial_json": final_args},
                        },
                    )
                )
            try:
                parsed = json.loads(block["arguments"] or "{}")
            except json.JSONDecodeError:
                parsed = {}
            self.content[block["content_index"]]["input"] = parsed if isinstance(parsed, dict) else {}
        block["closed"] = True
        events.append(("content_block_stop", {"type": "content_block_stop", "index": block["index"]}))
        return events

    def _finish(self, event: dict[str, Any], incomplete: bool = False) -> list[tuple[str, dict[str, Any]]]:
        if self.finished:
            return []
        events: list[tuple[str, dict[str, Any]]] = []
        for output_index in list(self.blocks):
            events.extend(self._close(output_index))
        events.extend(self._message_start())
        response = event.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            cached = 0
            details = usage.get("input_tokens_details")
            if isinstance(details, dict):
                cached = int(details.get("cached_tokens") or 0)
            self.usage = {
                "input_tokens": max(0, input_tokens - cached),
                "output_tokens": output_tokens,
            }
            if cached:
                self.usage["cache_read_input_tokens"] = cached
        self.stop_reason = "max_tokens" if incomplete else ("tool_use" if self.saw_tool else "end_turn")
        events.append(
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
                    "usage": self.usage,
                },
            )
        )
        events.append(("message_stop", {"type": "message_stop"}))
        self.finished = True
        return events

    def feed(self, event: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        kind = _string(event.get("type")) or ""
        if kind == "response.output_item.added":
            item = event.get("item")
            output_index = event.get("output_index", 0)
            return self._start_item(int(output_index), item) if isinstance(item, dict) else []
        if kind == "response.output_text.delta":
            output_index = self._output_index(event)
            block = self.blocks.get(output_index) if output_index is not None else None
            delta = _string(event.get("delta"))
            if not block or block["kind"] != "text" or not delta:
                return []
            block["text"] += delta
            self.content[block["content_index"]]["text"] = block["text"]
            return [
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": block["index"], "delta": {"type": "text_delta", "text": delta}},
                )
            ]
        if kind == "response.function_call_arguments.delta":
            output_index = self._output_index(event)
            block = self.blocks.get(output_index) if output_index is not None else None
            delta = _string(event.get("delta"))
            if not block or block["kind"] != "tool" or not delta:
                return []
            block["arguments"] += delta
            return [
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block["index"],
                        "delta": {"type": "input_json_delta", "partial_json": delta},
                    },
                )
            ]
        if kind == "response.function_call_arguments.done":
            output_index = self._output_index(event)
            block = self.blocks.get(output_index) if output_index is not None else None
            arguments = _string(event.get("arguments"))
            if block and block["kind"] == "tool" and arguments and not block["arguments"]:
                block["arguments"] = arguments
                return [
                    (
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": block["index"],
                            "delta": {"type": "input_json_delta", "partial_json": arguments},
                        },
                    )
                ]
            return []
        if kind == "response.output_item.done":
            output_index = int(event.get("output_index", 0))
            item = event.get("item")
            return self._close(output_index, item if isinstance(item, dict) else None)
        if kind in {"response.completed", "response.done"}:
            return self._finish(event)
        if kind == "response.incomplete":
            return self._finish(event, incomplete=True)
        if kind in {"response.failed", "error"}:
            raise ProxyError("OpenAI response failed", 502)
        return []


def sse_bytes(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def usage_summary(model: str, usage: dict[str, int]) -> str:
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_tokens = int(usage.get("cache_read_input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = input_tokens + cached_tokens + output_tokens
    return (
        f"Codex usage: model={model} input={input_tokens} cached={cached_tokens} "
        f"output={output_tokens} total={total_tokens}"
    )


def print_usage(model: str, usage: dict[str, int]) -> None:
    print(usage_summary(model, usage), flush=True)


def approximate_tokens(value: Any) -> int:
    # Deliberately local and dependency-free. This endpoint is used by Claude
    # Code for budgeting; the upstream response still supplies actual usage.
    total_chars = 0

    def visit(item: Any) -> None:
        nonlocal total_chars
        if isinstance(item, str):
            if len(item) < 1_000_000 or not re.fullmatch(r"[A-Za-z0-9+/=]+", item):
                total_chars += len(item)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                total_chars += len(str(key))
                visit(child)

    visit(value)
    return max(1, (total_chars + 3) // 4)


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "codex-proxy"
    sys_version = ""

    def log_message(self, _format: str, *_args: Any) -> None:
        # Never allow the base server to log request paths or caller metadata.
        return

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/", "/health", "/healthz"}:
            self._json(200, {"ok": True, "service": "codex-proxy"})
        else:
            self._json(404, error_document("Not found"))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._authorize()
            body = self._read_json()
            path = urlsplit(self.path).path
            debug(
                f"incoming path={path} model={body.get('model')!r} "
                f"stream={body.get('stream')!r} tools="
                f"{len(body.get('tools', [])) if isinstance(body.get('tools'), list) else 0}"
            )
            if path == "/v1/messages/count_tokens":
                self._json(200, {"input_tokens": approximate_tokens(body)})
                return
            if path != "/v1/messages":
                raise ProxyError("Not found", 404)
            settings = read_settings()
            auth = read_auth(settings)
            upstream_body = translate_request(body, settings)
            session_id = _string(self.headers.get("x-claude-code-session-id"))
            stream = open_upstream(upstream_body, auth, session_id)
            downstream_model = _string(body.get("model")) or settings.model
            try:
                if body.get("stream") is False:
                    self._non_streaming(stream, downstream_model, settings.model)
                else:
                    self._streaming(stream, downstream_model, settings.model)
            finally:
                stream.close()
        except ProxyError as exc:
            debug(f"request failed status={exc.status} detail={str(exc)!r}")
            self._json(exc.status, error_document(str(exc)))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # avoid leaking internals, paths, or auth values
            debug(f"internal error type={type(exc).__name__}")
            print(f"codex_proxy internal error: {type(exc).__name__}", file=sys.stderr)
            self._json(500, error_document("Internal proxy error"))

    def _authorize(self) -> None:
        expected = require_proxy_secret()
        supplied = self.headers.get("x-api-key") or self.headers.get("x-anthropic-api-key")
        authorization = self.headers.get("authorization", "")
        if not supplied and authorization.lower().startswith("bearer "):
            supplied = authorization[7:]
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise ProxyError("Unauthorized local proxy request", 401)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("content-length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise ProxyError("Invalid Content-Length", 400) from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ProxyError("Request body is empty or too large", 413 if length > 0 else 400)
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ProxyError("Request body is not valid JSON", 400) from exc
        if not isinstance(value, dict):
            raise ProxyError("Request body must be a JSON object", 400)
        return value

    def _streaming(self, stream: BinaryIO, model: str, usage_model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        translator = AnthropicTranslator(model)
        try:
            for upstream_event in iter_sse(stream):
                for event, data in translator.feed(upstream_event):
                    self.wfile.write(sse_bytes(event, data))
                    self.wfile.flush()
            if not translator.finished:
                raise ProxyError("OpenAI stream ended before a completion event", 502)
            print_usage(usage_model, translator.usage)
        except ProxyError as exc:
            debug(f"stream translation failed status={exc.status} detail={str(exc)!r}")
            data = {"type": "error", "error": {"type": "api_error", "message": str(exc)}}
            self.wfile.write(sse_bytes("error", data))
            self.wfile.flush()

    def _non_streaming(self, stream: BinaryIO, model: str, usage_model: str) -> None:
        translator = AnthropicTranslator(model)
        for upstream_event in iter_sse(stream):
            translator.feed(upstream_event)
        if not translator.finished:
            raise ProxyError("OpenAI stream ended before a completion event", 502)
        print_usage(usage_model, translator.usage)
        document = {
            "id": translator.message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": translator.content,
            "stop_reason": translator.stop_reason,
            "stop_sequence": None,
            "usage": translator.usage,
        }
        self._json(200, document)

    def _json(self, status: int, value: dict[str, Any]) -> None:
        if self.wfile.closed:
            return
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            return


def error_document(message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": "api_error", "message": message}}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def self_test() -> None:
    request = {
        "model": "ignored",
        "system": [{"type": "text", "text": "Be precise."}],
        "messages": [
            {"role": "user", "content": "Read a file"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call_old", "name": "Read", "input": {"file_path": "x"}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_old", "content": "hello"}],
            },
        ],
        "tools": [{"name": "Read", "description": "Read", "input_schema": {"type": "object"}}],
    }
    settings = CodexSettings(Path("."), "gpt-test", "high")
    translated = translate_request(request, settings)
    assert translated["model"] == "gpt-test"
    assert translated["instructions"] == "Be precise."
    assert any(item.get("type") == "function_call_output" for item in translated["input"])

    lite_settings = CodexSettings(Path("."), "gpt-5.6-sol", "high")
    lite_request = translate_request(request, lite_settings)
    assert "instructions" not in lite_request and "tools" not in lite_request
    assert lite_request["parallel_tool_calls"] is False
    assert lite_request["input"][0]["type"] == "additional_tools"
    assert lite_request["input"][1]["role"] == "developer"
    assert lite_request["reasoning"]["context"] == "all_turns"

    translator = AnthropicTranslator("gpt-test")
    fixture = [
        {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "m1"}},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "Hi"},
        {"type": "response.output_item.done", "output_index": 0, "item": {"type": "message"}},
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "function_call", "call_id": "call_1", "name": "Read"},
        },
        {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"file_path":"x"}'},
        {"type": "response.output_item.done", "output_index": 1, "item": {"type": "function_call"}},
        {
            "type": "response.completed",
            "response": {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "input_tokens_details": {"cached_tokens": 4},
                }
            },
        },
    ]
    emitted: list[tuple[str, dict[str, Any]]] = []
    for event in fixture:
        emitted.extend(translator.feed(event))
    assert translator.finished and translator.stop_reason == "tool_use"
    assert translator.content[0] == {"type": "text", "text": "Hi"}
    assert translator.content[1]["input"] == {"file_path": "x"}
    assert translator.usage == {
        "input_tokens": 6,
        "output_tokens": 3,
        "cache_read_input_tokens": 4,
    }
    assert usage_summary("gpt-test", translator.usage) == (
        "Codex usage: model=gpt-test input=6 cached=4 output=3 total=13"
    )
    assert emitted[0][0] == "message_start" and emitted[-1][0] == "message_stop"

    for endpoint in ALLOWED_UPSTREAM_ENDPOINTS:
        validate_upstream_endpoint(endpoint)
    for endpoint in (
        "http://api.openai.com/v1/responses",
        "https://api.openai.com:444/v1/responses",
        "https://api.openai.com/v1/responses/extra",
        "https://api.openai.com.example/v1/responses",
        "https://example.com/v1/responses",
    ):
        try:
            validate_upstream_endpoint(endpoint)
        except ProxyError:
            pass
        else:
            raise AssertionError(f"Unapproved endpoint was accepted: {endpoint}")

    redirect_handler = RejectRedirects()
    redirect_request = Request(OPENAI_RESPONSES_URL, data=b"{}", method="POST")
    for status in (301, 302, 303, 307, 308):
        try:
            redirect_handler.redirect_request(
                redirect_request,
                None,  # type: ignore[arg-type]
                status,
                "redirect",
                {},
                "https://example.com/collect",
            )
        except HTTPError as exc:
            assert exc.code == status
        else:
            raise AssertionError(f"HTTP {status} redirect was accepted")

    original_secret = os.environ.pop("CODEX_PROXY_SECRET", None)
    try:
        try:
            require_proxy_secret()
        except ProxyError:
            pass
        else:
            raise AssertionError("Missing CODEX_PROXY_SECRET was accepted")
        os.environ["CODEX_PROXY_SECRET"] = "self-test-local-secret"
        assert require_proxy_secret() == "self-test-local-secret"
    finally:
        if original_secret is None:
            os.environ.pop("CODEX_PROXY_SECRET", None)
        else:
            os.environ["CODEX_PROXY_SECRET"] = original_secret

    assert "redirect" in upstream_http_error_message(302).lower()
    assert "login" in upstream_http_error_message(401).lower()
    assert "secret-placeholder" not in upstream_http_error_message(500)

    failed = AnthropicTranslator("gpt-test")
    try:
        failed.feed({"type": "error", "error": {"message": "Bearer secret-placeholder"}})
    except ProxyError as exc:
        assert str(exc) == "OpenAI response failed"
    else:
        raise AssertionError("Upstream error event was accepted")

    print("Self-test passed")


def require_proxy_secret() -> str:
    secret = os.environ.get("CODEX_PROXY_SECRET")
    if not secret:
        raise ProxyError("CODEX_PROXY_SECRET is required; set it before starting the proxy", 500)
    return secret


def check_configuration() -> None:
    settings = read_settings()
    auth = read_auth(settings)
    print(f"Codex home: {settings.codex_home}")
    print(f"Model: {settings.model}")
    print(f"Reasoning effort: {settings.reasoning_effort or 'Codex default'}")
    print(f"Authentication: {auth.mode}")
    print(f"Account ID present: {'yes' if auth.account_id else 'not required'}")
    print(f"Local secret required: {'yes' if os.environ.get('CODEX_PROXY_SECRET') else 'no'}")
    print("Configuration is usable (no network request was sent)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Claude Code to Codex proxy")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CODEX_PROXY_PORT", DEFAULT_PORT)))
    parser.add_argument("--check", action="store_true", help="validate local config/auth without sending a request")
    parser.add_argument("--self-test", action="store_true", help="run offline translation tests")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.check:
        check_configuration()
        return 0
    if not 1 <= args.port <= 65535:
        raise ProxyError("Port must be between 1 and 65535")
    require_proxy_secret()
    settings = read_settings()
    read_auth(settings)  # fail before opening the listener; values are not retained
    server = LocalServer(("127.0.0.1", args.port), ProxyHandler)
    print(f"Codex proxy listening on http://127.0.0.1:{args.port}")
    print(f"Using configured model: {settings.model}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProxyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
