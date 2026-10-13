"""v08: Server-Sent Events(SSE) 스트리밍을 직접 파싱한다.

SSE 포맷 (HTTP 응답 본문이 이런 줄들로 "흘러" 온다)
    event: content_block_delta
    data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}}
    <빈 줄 = 이벤트 하나 끝>

스트리밍 응답을 조각조각 모아(accumulate) 최종적으로는 "비스트리밍 응답과 똑같은 dict"를 만든다.
그러면 v03의 from_anthropic_response / from_openai_response를 그대로 재사용할 수 있다.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator

from .base import APIError

OnText = Callable[[str], None] | None


def iter_sse(lines: Iterator[bytes]) -> Iterator[tuple[str, str]]:
    """바이트 줄들 → (event, data) 쌍."""
    event, data = "", []
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:  # 빈 줄 = 이벤트 경계
            if data:
                yield event, "\n".join(data)
            event, data = "", []
        elif line.startswith(":"):  # 주석 (keep-alive)
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


def post_sse(url: str, headers: dict[str, str], body: dict, timeout: float = 600) -> Iterator[tuple[str, str]]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("accept", "text/event-stream")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise APIError(e.code, e.read().decode("utf-8", errors="replace"), dict(e.headers or {})) from None
    with resp:
        yield from iter_sse(iter(resp.readline, b""))


# --------------------------------------------------------------------------- Anthropic
ANTHROPIC_ERROR_STATUS = {"overloaded_error": 529, "rate_limit_error": 429, "api_error": 500}


def accumulate_anthropic(events: Iterator[tuple[str, str]], on_text: OnText = None) -> dict:
    """Anthropic 스트림 이벤트 → 비스트리밍과 같은 모양의 응답 dict."""
    msg: dict = {"content": [], "stop_reason": None, "usage": {}}
    partial_json: dict[int, str] = {}
    for _, data in events:
        ev = json.loads(data)
        t = ev.get("type")
        if t == "message_start":
            m = ev["message"]
            msg.update({k: m[k] for k in ("id", "model", "role") if k in m})
            msg["usage"] = dict(m.get("usage") or {})
        elif t == "content_block_start":
            block = dict(ev["content_block"])
            if block.get("type") == "tool_use":
                block["input"] = {}
                partial_json[ev["index"]] = ""
            _set_block(msg["content"], ev["index"], block)
        elif t == "content_block_delta":
            block, d = msg["content"][ev["index"]], ev["delta"]
            dt = d.get("type")
            if dt == "text_delta":
                block["text"] = block.get("text", "") + d["text"]
                if on_text:
                    on_text(d["text"])
            elif dt == "input_json_delta":
                partial_json[ev["index"]] += d.get("partial_json", "")
            elif dt == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + d.get("thinking", "")
            elif dt == "signature_delta":
                block["signature"] = block.get("signature", "") + d.get("signature", "")
        elif t == "content_block_stop":
            i = ev["index"]
            if i in partial_json:
                raw = partial_json.pop(i)
                try:
                    msg["content"][i]["input"] = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    msg["content"][i]["input"] = {"__invalid_json__": raw}
        elif t == "message_delta":
            msg["stop_reason"] = ev.get("delta", {}).get("stop_reason", msg["stop_reason"])
            msg["usage"].update(ev.get("usage") or {})
        elif t == "error":
            err = ev.get("error", {})
            raise APIError(ANTHROPIC_ERROR_STATUS.get(err.get("type"), 500), json.dumps(ev))
    return msg


def _set_block(content: list[dict], index: int, block: dict) -> None:
    while len(content) <= index:
        content.append({})
    content[index] = block


# --------------------------------------------------------------------------- OpenAI 호환
def accumulate_openai(events: Iterator[tuple[str, str]], on_text: OnText = None) -> dict:
    """OpenAI chat.completion.chunk 스트림 → 비스트리밍 응답 dict."""
    message: dict = {"role": "assistant", "content": ""}
    tool_calls: dict[int, dict] = {}
    finish_reason, usage, model = None, None, ""
    for _, data in events:
        if data.strip() == "[DONE]":
            break
        chunk = json.loads(data)
        if "error" in chunk:
            raise APIError(500, json.dumps(chunk))
        model = chunk.get("model", model)
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            d = choice.get("delta") or {}
            if d.get("content"):
                message["content"] += d["content"]
                if on_text:
                    on_text(d["content"])
            if d.get("reasoning"):
                message["reasoning"] = message.get("reasoning", "") + d["reasoning"]
            if d.get("reasoning_details"):
                message.setdefault("reasoning_details", []).extend(d["reasoning_details"])
            for tc in d.get("tool_calls") or []:
                slot = tool_calls.setdefault(tc.get("index", 0), {"id": "", "type": "function",
                                                                  "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                slot["function"]["name"] += fn.get("name") or ""
                slot["function"]["arguments"] += fn.get("arguments") or ""
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return {"choices": [{"message": message, "finish_reason": finish_reason}], "usage": usage or {}, "model": model}
