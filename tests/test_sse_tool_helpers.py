import json
import unittest

from llama_web.core import sse_tool_helpers as helpers


BODY = {
    "tools": [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        },
    }]
}


class StreamedToolRepairTests(unittest.TestCase):
    def test_bare_json_tool_array_becomes_a_structured_call(self):
        text = '[{"name": "get_weather", "arguments": {"q": "Durham nc"}}]<end_of_turn>'
        clean, calls = helpers._repair_textual_tool_calls(text, BODY)
        self.assertEqual(clean, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"q": "Durham nc"})

    def test_stream_guard_turns_leaked_markup_into_tool_finish(self):
        content = '[{"name": "get_weather", "arguments": {"q": "Durham nc"}}]<end_of_turn>'
        lines = [
            b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": content}}]}).encode() + b"\n\n",
            b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        repaired = helpers._guard_streamed_tool_calls(lines, BODY)
        self.assertIsNotNone(repaired)
        event = json.loads(repaired.decode().splitlines()[0][6:])
        self.assertEqual(event["choices"][0]["finish_reason"], "tool_calls")
        call = event["choices"][0]["delta"]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "get_weather")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"q": "Durham nc"})


class TableMarkupTests(unittest.TestCase):
    def test_table_pseudo_tool_renders_as_a_markdown_table(self):
        payload = (
            '[{"name": "table", "arguments": {"data": ['
            '["Date", "High"], ["2026-11-24", "57.6"]]}}]<end_of_turn>'
        )
        clean, calls = helpers._repair_textual_tool_calls(payload, BODY)
        self.assertEqual(calls, [])
        self.assertIn("| Date | High |", clean)
        self.assertIn("| --- | --- |", clean)
        self.assertIn("| 2026-11-24 | 57.6 |", clean)

    def test_table_markup_stream_is_repaired_without_a_tool_round(self):
        payload = (
            '[{"name": "table", "arguments": {"data": ['
            '["Date", "High"], ["2026-11-24", "57.6"]]}}]<end_of_turn>'
        )
        lines = [
            b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": payload}}]}).encode() + b"\n\n",
            b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        repaired = helpers._guard_streamed_tool_calls(lines, BODY)
        self.assertIsNotNone(repaired)
        event = json.loads(repaired.decode().splitlines()[0][6:])
        self.assertEqual(event["choices"][0]["finish_reason"], "stop")
        self.assertIn("| Date | High |", event["choices"][0]["delta"]["content"])
        self.assertNotIn("name", event["choices"][0]["delta"])


class LeakedStreamTests(unittest.TestCase):
    def test_normal_stream_is_not_buffered_or_rewritten(self):
        lines = [
            'data: ' + json.dumps({"choices": [{"index": 0, "delta": {"content": "Hello"}}]}) + '\n\n',
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
            "data: [DONE]\n\n",
        ]
        self.assertEqual(list(helpers.repair_leaked_tool_stream(iter(lines), {})), lines)

    def test_table_markup_is_repaired_even_without_offered_tools(self):
        content = (
            '[{"name": "table", "arguments": {"data": ['
            '["Date", "High"], ["2026-11-24", "57.6"]]}}]<end_of_turn>'
        )
        lines = [
            'data: ' + json.dumps({"choices": [{"index": 0, "delta": {"content": content}}]}) + '\n\n',
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
            "data: [DONE]\n\n",
        ]
        out = list(helpers.repair_leaked_tool_stream(iter(lines), {"tools": []}))
        self.assertIn("| Date | High |", "".join(out))
        self.assertNotIn('"name": "table"', "".join(out))


if __name__ == "__main__":
    unittest.main()
