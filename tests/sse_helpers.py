"""Generic SSE/tool-call decoding helpers shared by compatibility tests."""
import json
import math


class Cancelled(Exception):
    """Sentinel used by cancellation tests."""


def function(name, properties, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required if required is not None else list(properties),
            },
        },
    }


WEATHER = function("get_weather", {"city": {"type": "string"}})


def decode_events(events):
    calls, text, finishes, usage = {}, [], [], {}
    for event in events:
        if not isinstance(event, dict):
            continue
        usage.update(event.get("usage") or {})
        timings = event.get("timings") or {}
        for source, target in (("predicted_per_second", "decoding_speed_tps"),
                               ("prompt_per_second", "prefill_speed_tps")):
            speed = timings.get(source)
            if isinstance(speed, (int, float)) and not isinstance(speed, bool) and math.isfinite(speed) and speed > 0:
                usage[target] = speed
        for choice in event.get("choices", []):
            message = choice.get("delta", choice.get("message", {}))
            content = message.get("content")
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if content:
                text.append(content)
            elif reasoning:
                text.append(reasoning)
            if choice.get("finish_reason"):
                finishes.append(choice["finish_reason"])
            for index, call in enumerate(message.get("tool_calls") or []):
                target = calls.setdefault(call.get("index", index), {"name": "", "arguments": "", "id": ""})
                fn = call.get("function") or {}
                target["name"] += fn.get("name") or ""
                target["arguments"] += fn.get("arguments") or ""
                target["id"] = call.get("id") or target["id"]
    for call in calls.values():
        assert call["id"], "Missing tool call ID"
        try:
            call["arguments"] = json.loads(call["arguments"])
        except json.JSONDecodeError as exc:
            raise ValueError(
                f'Invalid JSON arguments for {call["name"]}: {exc}; finish reasons={finishes}'
            ) from exc
    return list(calls.values()), "".join(text), finishes, usage


def decode(response, stream):
    response.raise_for_status()
    if stream:
        content = response.content
        source = content.decode("utf-8") if isinstance(content, bytes) else response.text
        assert "data: [DONE]" in source, "Missing SSE terminator"
        events = [json.loads(line[6:]) for line in source.split("\n")
                  if line.startswith("data: ") and line != "data: [DONE]"]
    else:
        events = [response.json()]
    return decode_events(events)
