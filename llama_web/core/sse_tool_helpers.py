"""Shared SSE/tool-call repair helpers used by the local backend shims.

Moved out of the retired FastFlowLM shim so the LM Studio shims keep the same
behaviour without depending on that backend.
"""
import json
import re

from llama_web.compat import exact_file_content
from llama_web.compat import exact_tool_contract
from llama_web.compat import gemma_tool_arguments
from llama_web.compat import question_tool_compat
from llama_web.compat import tool_argument_json

_TEXT_TOOL_BLOCK_RE = re.compile(r"<tool_call\b[^>]*>(.*?)</tool_call>", re.I | re.S)
_TEXT_FUNCTION_RE = re.compile(
    r"<function\s*=\s*([^>\s]+)\s*>(.*?)(?:</function>|$)", re.I | re.S
)
_TEXT_PARAMETER_RE = re.compile(
    r"<parameter\s*=\s*([^>\s]+)\s*>(.*?)(?:</parameter>|$)", re.I | re.S
)
_TEXT_DIRECT_ARG_RE = re.compile(r"<([A-Za-z_][\w.-]*)\s*=\s*([^<>\r\n]+)>")
_TEXT_FUNCTION_ELEMENT_RE = re.compile(r'<function>\s*([\w.-]+)\s*</function>', re.I)
_TEXT_PARAMETER_ELEMENT_RE = re.compile(r'<parameter>(.*?)</parameter>', re.I | re.S)
_JSON_FENCE_RE = re.compile(r"```json[^\n]*\n(.*?)```", re.I | re.S)
_GEMMA_TOOL_RE = re.compile(r'<\|tool_call>call:([\w.-]+)(\{.*?\})(?:<tool_call\|>|<turn\|>)', re.S)
# Qwen-style native markup: <tool_calls:id><tool_call:id>NAME<arg_key:id>k</arg_key:id>
# <arg_value:id>v</arg_value:id></tool_call:id></tool_calls:id>
_QWEN_TOOLS_RE = re.compile(r"<tool_calls:[^>]*>(.*?)</tool_calls:[^>]*>", re.S)
_QWEN_CALL_RE = re.compile(r"<tool_call:[^>]*>(.*?)</tool_call:[^>]*>", re.S)
_QWEN_ARG_RE = re.compile(
    r"<arg_key:[^>]*>(.*?)</arg_key:[^>]*>\s*<arg_value:[^>]*>(.*?)</arg_value:[^>]*>", re.S)
_TEXT_TOOL_MARKERS = ("<tool_call", "<|tool_call", "```json", "<tool_calls:")


def _inject_tools_block(body: dict) -> dict:
    """If the request carries `tools`, prepend/merge the rendered block into the first system
    message (or add one) — same placement chat_template.jinja itself uses. No-op otherwise."""
    tools = body.get("tools")
    if not tools or body.get("tool_choice") == "none":
        return body
    model = str(body.get("model") or "").removeprefix("flm/")
    if model == "minicpm-v-4.7:1b":
        instruction = (
            "FastFlowLM MiniCPM tool contract: when a user requests an offered function, "
            "return the function call in the model's native tool format. Do not replace a "
            "tool call with Python, shell code, JSON examples, or a claim that an action ran. "
            "Use the exact function name and arguments requested. If the user asks for a "
            "direct text answer or says to stop using tools, do not emit a function call."
        )
        messages = list(body.get("messages") or [])
        if messages and messages[0].get("role") == "system":
            messages[0] = {**messages[0], "content": instruction + "\n\n" + (messages[0].get("content") or "")}
        else:
            messages.insert(0, {"role": "system", "content": instruction})
        return {**body, "messages": messages}
    # Qwen3.8's current runtime renders the tools itself. Injecting the legacy
    # workaround as well includes every schema twice and overflows its 8K window.
    # Keep the workaround for older runtimes/models that still require it.
    if model.startswith("qwen3.8"):
        return body
    if model.startswith(("qwen3:", "qwen3-it:")):
        block = ("# Tools\n\nFunction signatures:\n<tools>\n" +
                 "\n".join(json.dumps(tool) for tool in tools) +
                 '\n</tools>\nFor each function call return a JSON object within <tool_call> tags:\n'
                 '<tool_call>\n{"name": "function_name", "arguments": {"parameter": "value"}}\n</tool_call>\n'
                 'Use exactly the names and parameters in the offered schemas. Read project files before editing.')
    else:
        block = _tools_system_block(tools)
    block += (
        '\nOmit optional arguments unless they are needed for the requested operation. '
        'For a whole-file read, send only the declared path field; omit offset and limit. '
        'Use offset/limit only for an explicitly requested page or to continue a truncated read. '
        'Omit replaceAll unless the user explicitly requests replacing all matches. '
        'When exact arguments are supplied, do not add extra fields.'
    )
    messages = list(body.get("messages") or [])
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content") or ""
        messages[0] = {**messages[0], "content": f"{block}\n\n{existing}" if existing else block}
    else:
        messages = [{"role": "system", "content": block}] + messages
    return {**body, "messages": messages}


_DEFAULT_FREQUENCY_PENALTY = 0.4


_TEXT_TOOL_BLOCK_RE = re.compile(r"<tool_call\b[^>]*>(.*?)</tool_call>", re.I | re.S)
_TEXT_FUNCTION_RE = re.compile(
    r"<function\s*=\s*([^>\s]+)\s*>(.*?)(?:</function>|$)", re.I | re.S
)
_TEXT_PARAMETER_RE = re.compile(
    r"<parameter\s*=\s*([^>\s]+)\s*>(.*?)(?:</parameter>|$)", re.I | re.S
)
_TEXT_DIRECT_ARG_RE = re.compile(r"<([A-Za-z_][\w.-]*)\s*=\s*([^<>\r\n]+)>")
_TEXT_FUNCTION_ELEMENT_RE = re.compile(r'<function>\s*([\w.-]+)\s*</function>', re.I)
_TEXT_PARAMETER_ELEMENT_RE = re.compile(r'<parameter>(.*?)</parameter>', re.I | re.S)
# JSON code fences are how small models (observed 2026-10: LM Studio qwen3.8-2b-distill,
# exact-args suite) emit tool calls as plain text: a single ```json object, or a
# ```json array for parallel calls. Only json-tagged fences qualify; ```bash and
# ```python blocks are answer code, never call markup.
_JSON_FENCE_RE = re.compile(r"```json[^\n]*\n(.*?)```", re.I | re.S)
_GEMMA_TOOL_RE = re.compile(r'<\|tool_call>call:([\w.-]+)(\{.*?\})(?:<tool_call\|>|<turn\|>)', re.S)

def _markdown_table(data) -> str:
    """Render a 2-D array emitted as a `table` tool call as a GFM pipe table."""
    if not isinstance(data, list) or not data:
        return ""
    rows = [[str(cell) for cell in row] for row in data if isinstance(row, list)]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    def line(cells):
        return "| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |"

    rendered = [line(rows[0]), "| " + " | ".join(["---"] * width) + " |"]
    rendered.extend(line(row) for row in rows[1:])
    return "\n".join(rendered)


def _repair_textual_tool_calls(text: str, body: dict) -> tuple[str, list[dict]]:
    """Convert model-native tool markup leaked as content into OpenAI tool calls.

    Handles XML <tool_call> blocks, Gemma native tool markers, and ```json fences
    carrying one call object or an array of call objects. A fenced object without a
    name is only accepted when the request itself forces exactly one function,
    so an ordinary JSON answer can never become an executable call. Only names
    present in the request's tool schemas are accepted.
    """
    allowed = {
        str((tool.get("function") or {}).get("name"))
        for tool in body.get("tools") or []
        if (tool.get("function") or {}).get("name")
    }
    if body.get("tool_choice") == "none":
        return text, []
    schemas = {t["function"]["name"]: t["function"].get("parameters", {})
               for t in body.get("tools") or [] if (t.get("function") or {}).get("name")}

    def spec_from_value(value):
        if not isinstance(value, dict):
            return None
        fn = value.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            arguments = fn.get("arguments")
        else:
            name = value.get("name") or (fn if isinstance(fn, str) else None)
            arguments = value.get("arguments")
            if arguments is None:
                arguments = {k: v for k, v in value.items() if k not in {"function", "name"}}
        if isinstance(name, str) and name not in allowed:
            # Local GGUFs sometimes drop the MCP server prefix (e.g. emit
            # `browser_navigate` instead of `playwright__browser_navigate`).
            name = next((offered for offered in allowed
                         if offered.split("__", 1)[-1] == name), name)
        if not isinstance(name, str) or name not in allowed:
            return None
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"value": arguments}
        if not isinstance(arguments, dict):
            arguments = {}
        return name, arguments

    def forced_name():
        choice = body.get("tool_choice")
        if isinstance(choice, dict) and choice.get("type") == "function":
            name = (choice.get("function") or {}).get("name")
            return name if name in allowed and name in schemas else None
        if choice == "required" and len(schemas) == 1:
            (only,) = schemas.keys()
            return only
        return None

    # Qwen-family GGUFs sometimes emit their native tool markup as content instead
    # of a structured tool_calls delta. Recover it so the call is dispatched normally.
    qwen_block = _QWEN_TOOLS_RE.search(text or "")
    if qwen_block:
        calls = []
        for block in _QWEN_CALL_RE.findall(qwen_block.group(1)):
            name = re.split(r"<arg_key:", block, maxsplit=1)[0].strip()
            arguments = {}
            for key, value in _QWEN_ARG_RE.findall(block):
                try:
                    arguments[key] = json.loads(value)
                except (TypeError, ValueError):
                    arguments[key] = value
            spec = spec_from_value({"name": name, "arguments": arguments})
            if spec:
                calls.append({
                    "id": f"qwen_{len(calls)}", "type": "function",
                    "function": {"name": spec[0], "arguments": json.dumps(spec[1])},
                })
        if calls:
            return text.replace(qwen_block.group(0), "").strip(), calls

    # Some local GGUF chat templates leak a bare JSON tool array as ordinary content,
    # optionally followed by a native end-of-turn marker. Accept it only when the whole
    # remaining reply parses as tool calls for names offered in this request.
    bare = re.sub(r"</?(?:end_of_turn|im_end|think|tool_call)>?", "", (text or "")).strip()
    if bare.startswith(("[", "{")):
        try:
            value = json.loads(bare)
        except (TypeError, ValueError):
            value = None
        candidates = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
        specs = []
        rendered_tables = []
        recognised = bool(candidates)
        for item in candidates:
            name = item.get("name") if isinstance(item, dict) else None
            args = item.get("arguments") if isinstance(item, dict) else None
            if name == "table" and isinstance(args, dict) and isinstance(args.get("data"), list):
                table = _markdown_table(args["data"])
                if table:
                    rendered_tables.append(table)
                    continue
            spec = spec_from_value(item)
            if not spec:
                recognised = False
                break
            specs.append(spec)
        if recognised and (specs or rendered_tables):
            calls = []
            for name, arguments in specs:
                arguments = _restore_schema_types(arguments, schemas.get(name, {}))
                calls.append({
                    "id": f"call_repaired_{len(calls) + 1}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments, separators=(",", ":"))},
                })
            # A `table` pseudo-tool is rendered for the user, not sent back to the model.
            return "\n\n".join(rendered_tables), calls

    entries = []  # (span, [specs]) in document order; spans removed after collection

    for match in _GEMMA_TOOL_RE.finditer(text or ''):
        if match.group(1) not in allowed:
            continue
        from llama_web.compat import gemma_tool_arguments
        try:
            arguments = gemma_tool_arguments.loads(match.group(2))
        except (ValueError, RecursionError):
            continue
        entries.append((match.span(), [(match.group(1), arguments)]))

    for match in _TEXT_TOOL_BLOCK_RE.finditer(text or ""):
        payload = match.group(1).strip()
        specs = []
        try:
            value = json.loads(payload)
        except (TypeError, ValueError):
            value = None
        if isinstance(value, dict):
            spec = spec_from_value(value)
            if spec:
                specs.append(spec)
        if not specs:
            element = _TEXT_FUNCTION_ELEMENT_RE.search(payload)
            if element and element.group(1) in allowed:
                name = element.group(1)
                properties = schemas[name].get('properties') or {}
                parameters = _TEXT_PARAMETER_ELEMENT_RE.findall(payload)
                if len(properties) == len(parameters) == 1:
                    specs.append((name, {next(iter(properties)): parameters[0].strip()}))
                elif not properties and not parameters:
                    specs.append((name, {}))
        if not specs:
            fn_match = _TEXT_FUNCTION_RE.search(payload)
            if fn_match:
                name = fn_match.group(1).strip().strip('"\'')
                fn_body = fn_match.group(2)
                arguments = {
                    p.group(1).strip(): p.group(2).strip()
                    for p in _TEXT_PARAMETER_RE.finditer(fn_body)
                }
                # Recover the malformed but frequent shorthand dial=example.com>.
                if not arguments:
                    arguments = {
                        p.group(1).strip(): p.group(2).strip().strip('"\'')
                        for p in _TEXT_DIRECT_ARG_RE.finditer(fn_body)
                        if p.group(1).lower() not in {"function", "parameter"}
                    }
                if name in allowed:
                    if not isinstance(arguments, dict):
                        arguments = {}
                    specs.append((name, arguments))
        if specs:
            entries.append((match.span(), specs))

    for match in _JSON_FENCE_RE.finditer(text or ""):
        try:
            value = json.loads(match.group(1))
        except (TypeError, ValueError):
            continue
        if isinstance(value, list):
            specs = [spec_from_value(item) for item in value if isinstance(item, dict)]
            # Only convert when every array item resolves to an allowed tool.
            if specs and all(s is not None for s in specs) and len(specs) == len(value):
                entries.append((match.span(), specs))
        else:
            spec = spec_from_value(value) if isinstance(value, dict) else None
            if spec is None and isinstance(value, dict) and value.get("name") is None \
                    and value.get("arguments") is None:
                forced = forced_name()
                if forced:
                    spec = (forced, value)
            if spec:
                entries.append((match.span(), [spec]))

    if not entries:
        return text, []

    entries.sort(key=lambda entry: entry[0])
    # A JSON fence may itself occur inside a tool_call block.
    entries = [entry for index, entry in enumerate(entries)
               if not any(outer[0][0] <= entry[0][0] and entry[0][1] <= outer[0][1]
                          for outer in entries[:index])]

    calls = []
    for span, specs in entries:
        for name, arguments in specs:
            arguments = _restore_schema_types(arguments, schemas.get(name, {}))
            calls.append({
                "id": f"call_repaired_{len(calls) + 1}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, separators=(",", ":"))},
            })
    clean_parts, cursor = [], 0
    for span, _specs in entries:
        start, stop = span
        clean_parts.append(text[cursor:start])
        cursor = stop
    clean_parts.append(text[cursor:])
    return ''.join(clean_parts).strip(), calls

def _repair_streamed_textual_tool_calls(raw_lines: list[bytes], body: dict) -> bytes | None:
    """Return a replacement SSE stream when raw content contains leaked tool calls."""
    content = []
    has_structured_calls = False
    template = None
    usage = None
    for raw_line in raw_lines:
        if not raw_line.startswith(b"data: "):
            continue
        payload = raw_line[len(b"data: "):].strip()
        if not payload or payload == b"[DONE]":
            continue
        try:
            obj = json.loads(payload)
            usage = obj.get('usage') or usage
            choice = obj["choices"][0]
            delta = choice.get("delta") or {}
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        template = obj
        has_structured_calls = has_structured_calls or bool(delta.get("tool_calls"))
        if delta.get("content"):
            content.append(delta["content"])
    if has_structured_calls:
        return None
    clean, calls = _repair_textual_tool_calls("".join(content), body)
    if not calls:
        return None
    base = {k: v for k, v in (template or {}).items() if k not in {"choices", "usage"}}
    pieces = []
    if clean:
        pieces.append(b"data: " + json.dumps({**base, "choices": [{"index": 0, "delta": {"content": clean}, "finish_reason": None}]}, separators=(",", ":")).encode() + b"\n\n")
    pieces.append(b"data: " + json.dumps({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{**tc, "index": i} for i, tc in enumerate(calls)]}, "finish_reason": "tool_calls"}]}, separators=(",", ":")).encode() + b"\n\n")
    if usage:
        pieces.append(b'data: ' + json.dumps({**base, 'choices': [], 'usage': usage}).encode() + b'\n\n')
    pieces.append(b"data: [DONE]\n\n")
    return b"".join(pieces)

def _streamable_text_prefix(pending: str) -> tuple[str, str]:
    """Release ordinary text while holding possible textual tool-call markup."""
    lowered = pending.casefold()
    positions = [lowered.find(marker) for marker in _TEXT_TOOL_MARKERS
                 if lowered.find(marker) >= 0]
    if positions:
        boundary = min(positions)
        return pending[:boundary], pending[boundary:]
    hold = 0
    for marker in _TEXT_TOOL_MARKERS:
        for size in range(1, min(len(marker), len(pending) + 1)):
            if lowered.endswith(marker[:size]):
                hold = max(hold, size)
    return (pending[:-hold], pending[-hold:]) if hold else (pending, "")

def _live_content_event(obj: dict, content: str) -> bytes:
    """Build an OpenAI-compatible content-only delta for a live client."""
    choice = (obj.get("choices") or [{}])[0]
    event = {key: value for key, value in obj.items() if key not in {"choices", "usage"}}
    event["choices"] = [{**choice, "delta": {"content": content}, "finish_reason": None}]
    return b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n"

def _remove_streamed_content_prefix(raw_lines, prefix: str):
    """Remove text already sent live from a repaired/buffered SSE replay."""
    if not prefix:
        return raw_lines
    raw = raw_lines if isinstance(raw_lines, bytes) else b"".join(raw_lines)
    remaining = prefix
    result = []
    for block in re.split(br"\r?\n\r?\n", raw):
        if not block:
            continue
        data_index = next((i for i, line in enumerate(block.splitlines())
                           if line.startswith(b"data:")), None)
        lines = block.splitlines()
        if data_index is None:
            result.append(block + b"\n\n")
            continue
        data_line = lines[data_index]
        payload = data_line[5:].strip()
        try:
            event = json.loads(payload)
        except (ValueError, TypeError):
            result.append(block + b"\n\n")
            continue
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if not isinstance(content, str) or not remaining:
                continue
            common = 0
            while common < min(len(content), len(remaining)) and content[common] == remaining[common]:
                common += 1
            if common:
                content = content[common:]
                remaining = remaining[common:]
                if content:
                    delta["content"] = content
                else:
                    delta.pop("content", None)
                if not delta and choice.get("finish_reason") is None:
                    choice["delta"] = {}
        choices = event.get("choices") or []
        if choices and all(not (choice.get("delta") or choice.get("finish_reason"))
                           for choice in choices) and not event.get("usage"):
            continue
        lines[data_index] = b"data: " + json.dumps(event, separators=(",", ":")).encode()
        result.append(b"\n".join(lines) + b"\n\n")
    return result


def _repair_tool_arguments(obj: dict, request_body: dict) -> bool:
    """Normalize aliases and neutralize malformed file operations before OpenCode sees them."""
    try:
        choice = obj["choices"][0]
        calls = (choice.get("delta") or choice.get("message") or {}).get("tool_calls") or []
    except (KeyError, IndexError, TypeError):
        return False
    if request_body.get("tool_choice") == "none" and calls:
        key = "delta" if "delta" in choice else "message"
        message = {k: v for k, v in choice[key].items() if k != "tool_calls"}
        message["content"] = message.get("content") or "Tool calling is disabled for this request."
        choice[key] = message
        choice["finish_reason"] = "stop"
        return True
    named_choice_repaired = _enforce_minicpm_named_call(obj, request_body)
    if named_choice_repaired:
        calls = (choice.get("delta") or choice.get("message") or {}).get("tool_calls") or []
    parallel_repaired = _repair_minicpm_parallel_calls(obj, request_body)
    if parallel_repaired:
        calls = (choice.get("delta") or choice.get("message") or {}).get("tool_calls") or []
    available = {
        ((tool.get("function") or {}).get("name") or "").lower()
        for tool in (request_body.get("tools") or [])
    }
    schemas = {
        str((tool.get("function") or {}).get("name") or "").lower():
            (tool.get("function") or {}).get("parameters") or {}
        for tool in request_body.get("tools") or []
    }
    recent_history = "\n".join(
        str(message.get("content") or "")
        for message in (request_body.get("messages") or [])[-12:]
    )
    repeated_replace_failure = len(re.findall(
        r"(?:replace_in_file|SEARCH block|exact match).{0,180}"
        r"(?:fail|not found|did not match|exact matching)",
        recent_history, re.I | re.S,
    )) >= 2 or bool(re.search(
        r"failed twice.{0,300}(?:write_to_file|write to file)",
        recent_history, re.I | re.S,
    ))
    user_requested_install = any(
        isinstance(message.get("content"), str)
        and re.search(r"\b(?:install|add dependency|add package)\b", message["content"], re.I)
        for message in (request_body.get("messages") or [])
        if message.get("role") == "user"
    )
    repaired = exact_tool_contract.repair_completion(obj, request_body) or named_choice_repaired or parallel_repaired
    for call in calls:
        function = call.get("function") or {}
        original_name = function.get("name")
        name = str(original_name or "").strip().strip("\"'").lower()
        if name != original_name and name in available:
            function["name"] = name
            repaired = True
        if name in ("execute", "bash", "shell") and name not in available:
            target = next((candidate for candidate in ("shell", "bash") if candidate in available), None)
            if target:
                name = target
                function["name"] = target
                repaired = True
        tool_kind = (
            "edit" if name in ("edit", "replace_in_file", "replace") else
            "write" if name in ("write", "write_to_file", "create_file") else
            "read" if name in ("read", "read_file") else
            "patch" if name in ("patch", "apply_patch") else
            name
        )
        raw = function.get("arguments")
        try:
            args = tool_argument_json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(args, dict):
            continue

        properties = schemas.get(name, {}).get("properties") or {}
        if tool_kind == "edit":
            for canonical, alias in (("oldString", "old_string"), ("newString", "new_string"),
                                     ("replaceAll", "replace_all")):
                if canonical in properties and canonical not in args and alias in args:
                    args[canonical] = args.pop(alias)
                    repaired = True
        path_key = next((key for key in ("path", "filePath", "file_path", "filepath", "filename", "file")
                         if key in properties), None)
        # Older clients omit schemas in some requests; preserve their existing spelling.
        if not properties:
            path_key = next((key for key in ("filePath", "path", "file_path", "filepath", "filename", "file")
                             if key in args), "filePath")
        # Patch tools embed file paths inside patchText; do not invent a path argument.
        if tool_kind in ("read", "write", "edit", "patch") and path_key:
            if path_key not in args:
                for alias in ("path", "filePath", "file_path", "filepath", "filename", "file"):
                    if isinstance(args.get(alias), str) and args[alias]:
                        args[path_key] = args.pop(alias)
                        repaired = True
                        break
            path = args.get(path_key)
            malformed_path = bool(
                not isinstance(path, str)
                or not path.strip()
                or "\n" in path
                or len(path) > 1000
                or re.search(r"[\"'](?:,|>)", path)
            )
            missing_content = tool_kind == "write" and not isinstance(args.get("content"), str)
            if malformed_path or missing_content:
                reason = (
                    f"llama_web blocked malformed {name} arguments. Retry {name} with {path_key} "
                    "containing only the absolute path and content in its separate parameter."
                )
                return _stop_blocked_tool(choice, reason)

            if tool_kind == "write" and repeated_replace_failure:
                reason = (
                    "llama_web blocked a whole-file write fallback after repeated exact-match "
                    "failures. Re-read the existing file, then use a smaller exact replacement "
                    "or structured patch; preserve unrelated content."
                )
                return _stop_blocked_tool(choice, reason)

        if name in ("bash", "shell"):
            if "command" not in args:
                for alias in ("cmd", "script", "input"):
                    if isinstance(args.get(alias), str) and args[alias]:
                        args["command"] = args.pop(alias)
                        repaired = True
                        break
            command = args.get("command")
            has_file_tool = any(
                token in tool_name for tool_name in available
                for token in ("write", "edit", "patch")
            )
            shell_file_write = bool(
                isinstance(command, str) and has_file_tool and (
                    re.search(r"(?:^|[;&|]\s*)cat\s+.*(?:>|>>|<<)", command)
                    or re.search(r"\bpython(?:3)?\s+-c\b.*\b(?:write|write_text)\b", command, re.S)
                    or re.search(r"\bsed\s+-i\b|(?:^|[^<])>{1,2}\s*\S+", command)
                )
            )
            unsolicited_install = bool(
                isinstance(command, str)
                and not user_requested_install
                and re.search(r"\b(?:pip|pip3)\s+install\b|playwright\s+install\b", command)
            )
            if shell_file_write or unsolicited_install:
                reason = (
                    "llama_web blocked an unsafe shell file-write/package-install command. "
                    "Use the structured write/edit tool, or report a missing dependency."
                )
                return _stop_blocked_tool(choice, reason)

        args = _restore_schema_types(args, schemas.get(name, {}))
        if name == "question":
            args = question_tool_compat.normalize(args, schemas.get(name, {}))
        serialized = json.dumps(args, separators=(",", ":"))
        if serialized != raw and function.get("name") == name:
            function["arguments"] = serialized
            repaired = True
    return exact_file_content.repair_completion(obj, request_body) or repaired


def _tools_system_block(tools: list[dict]) -> str:
    rendered = "".join("\n" + json.dumps(t) for t in tools)
    return _TOOLS_BLOCK_TMPL.format(tools=rendered) + _PARAMETER_TAG_WARNING


def _restore_schema_types(value, schema):
    """Restore unambiguous runtime string encodings using the offered schema."""
    expected = schema.get("type")
    if isinstance(value, str) and expected in ("boolean", "integer", "number", "object", "array", "null"):
        candidate = value.strip()
        if expected == "boolean" and candidate.lower() in ("true", "false"):
            value = candidate.lower() == "true"
        else:
            try:
                parsed = json.loads(candidate)
            except ValueError:
                parsed = value
            valid = {"integer": type(parsed) is int,
                     "number": type(parsed) in (int, float),
                     "boolean": type(parsed) is bool,
                     "object": isinstance(parsed, dict),
                     "array": isinstance(parsed, list), "null": parsed is None}
            if valid.get(expected, False):
                value = parsed
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        return {key: _restore_schema_types(item, properties.get(key, {}))
                for key, item in value.items()}
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            return [_restore_schema_types(item, items) for item in value]
    return value


def _stop_blocked_tool(choice: dict, reason: str) -> bool:
    key = "delta" if "delta" in choice else "message"
    choice[key] = {"role": "assistant", "content": reason + " No command was executed. This turn has stopped; revise the request before continuing."}
    choice["finish_reason"] = "stop"
    return True


def _enforce_minicpm_named_call(obj: dict, request_body: dict) -> bool:
    """Honor an exact named choice for MiniCPM, using only arguments supplied by the user."""
    if not _is_minicpm_47(request_body):
        return False
    choice = request_body.get("tool_choice")
    function = choice.get("function") if isinstance(choice, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    if not isinstance(name, str) or choice.get("type") != "function":
        return False
    contract = _minicpm_named_request(request_body)
    if contract is None or contract[0] != name:
        return False
    _requested_name, arguments, schema = contract
    if not isinstance(arguments, dict) or not set(schema.get("required") or []).issubset(arguments):
        return False
    try:
        result = obj["choices"][0]
    except (KeyError, IndexError, TypeError):
        return False
    key = "delta" if "delta" in result else "message"
    message = result.setdefault(key, {})
    calls = message.get("tool_calls") or []
    call = calls[0] if calls else {"id": f"call_llama_web_{time.time_ns()}", "type": "function"}
    desired_args = json.dumps(arguments, separators=(",", ":"))
    fn = call.setdefault("function", {})
    changed = (len(calls) != 1 or fn.get("name") != name or fn.get("arguments") != desired_args)
    fn.update(name=name, arguments=desired_args)
    call.setdefault("id", f"call_llama_web_{time.time_ns()}")
    call.setdefault("type", "function")
    message["tool_calls"] = [call]
    if changed:
        message["content"] = None
        result["finish_reason"] = "tool_calls"
    return changed


def _repair_minicpm_parallel_calls(obj: dict, request_body: dict) -> bool:
    if not _is_minicpm_47(request_body):
        return False
    contract = _minicpm_parallel_arguments(request_body)
    if contract is None:
        return False
    name, argument_name, values = contract
    try:
        result = obj["choices"][0]
    except (KeyError, IndexError, TypeError):
        return False
    key = "delta" if "delta" in result else "message"
    message = result.setdefault(key, {})
    existing = list(message.get("tool_calls") or [])
    calls = existing[:2]
    changed = len(existing) != 2
    while len(calls) < 2:
        calls.append({"id": f"call_llama_web_{time.time_ns()}_{len(calls)}", "type": "function"})
    for index, (call, value) in enumerate(zip(calls, values)):
        function = call.setdefault("function", {})
        arguments = json.dumps({argument_name: value}, separators=(",", ":"))
        if function.get("name") != name or function.get("arguments") != arguments:
            changed = True
        function.update(name=name, arguments=arguments)
        call.setdefault("id", f"call_llama_web_{time.time_ns()}_{index}")
        call.setdefault("type", "function")
    message["tool_calls"] = calls
    if changed:
        message["content"] = None
        result["finish_reason"] = "tool_calls"
    return changed


def _is_minicpm_47(request_body: dict) -> bool:
    model = str(request_body.get("model") or "").removeprefix("flm/")
    return model == "minicpm-v-4.7:1b"


def _minicpm_named_request(body: dict):
    """Read an explicit MiniCPM named-tool request, including the checkpoint shorthand."""
    contract = exact_tool_contract._request(body)
    if contract is not None:
        return contract
    choice = body.get("tool_choice")
    fn = choice.get("function") if isinstance(choice, dict) else None
    name = fn.get("name") if isinstance(fn, dict) else None
    if choice.get("type") != "function" or name not in ("bash", "shell"):
        return None
    user = next((str(message.get("content") or "") for message in reversed(body.get("messages") or [])
                 if message.get("role") == "user"), "")
    match = re.search(r"\bNext call\s+(?:bash|shell)\s+with command\s+(.+?)\s+and workdir\s+([^\s.]+)\s*\.?$",
                      user, re.I | re.S)
    if not match:
        return None
    return name, {"command": match.group(1).strip(), "workdir": match.group(2)}, \
        next((tool.get("function", {}).get("parameters") or {} for tool in body.get("tools") or []
              if (tool.get("function") or {}).get("name") == name), {})


def _minicpm_parallel_arguments(body: dict):
    """Recover two literal values only when MiniCPM's request explicitly names both."""
    if (not _is_minicpm_47(body) or body.get("parallel_tool_calls") is not True
            or body.get("tool_choice") not in (None, "auto", "required")):
        return None
    tools = body.get("tools") or []
    if len(tools) != 1:
        return None
    function = tools[0].get("function") or {}
    name = function.get("name")
    schema = function.get("parameters") or {}
    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    if len(required) != 1 or len(properties) != 1 or required[0] not in properties \
            or properties[required[0]].get("type") != "string":
        return None
    user = next((str(message.get("content") or "") for message in reversed(body.get("messages") or [])
                 if message.get("role") == "user"), "")
    match = re.search(rf"\bTWO\s+{re.escape(str(name))}\s+tool calls?\s*:\s*([^,.\n]+?)\s+first,\s*then\s+([^.\n]+)",
                      user, re.I)
    if not match:
        return None
    values = [match.group(1).strip(), match.group(2).strip()]
    if not all(values):
        return None
    return name, required[0], values


def _guard_streamed_tool_calls(raw_lines: list[bytes], body: dict) -> bytes | None:
    """Validate complete calls, including arguments split across SSE chunks."""
    calls = {}
    text = []
    template = {}
    usage = None
    finish = "tool_calls"
    for line in raw_lines:
        if not line.startswith(b"data: "):
            continue
        try:
            obj = json.loads(line[6:])
        except ValueError:
            continue
        if obj.get("usage"):
            usage = obj["usage"]
        for choice in obj.get("choices", []):
            template = {k: v for k, v in obj.items() if k not in {"choices", "usage"}}
            delta = choice.get("delta") or {}
            if delta.get("content"):
                text.append(delta["content"])
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            for call in delta.get("tool_calls") or []:
                index = call.get("index", 0)
                target = calls.setdefault(index, {"index": index, "type": "function",
                                                   "function": {"name": "", "arguments": ""}})
                if call.get("id"):
                    target["id"] = call["id"]
                for key in ("name", "arguments"):
                    target["function"][key] += (call.get("function") or {}).get(key) or ""
    stream_changed = False
    content_changed = False
    recovered_markup = False
    for index, call in list(calls.items()):
        fn = call["function"]
        if not fn["name"] and fn["arguments"].strip() in ("", "{}"):
            del calls[index]
            stream_changed = True
        elif not call.get("id"):
            call["id"] = f"call_llama_web_{time.time_ns()}_{index}"
            stream_changed = True
    if not calls and stream_changed:
        choice = {"index": 0, "delta": {"content": "".join(text)}, "finish_reason": "stop"}
        if _repair_tool_arguments({"choices": [choice]}, body):
            calls = {i: call for i, call in enumerate(choice["delta"].get("tool_calls") or [])}
            text = []
            finish = "tool_calls"
            recovered_markup = True
        else:
            event = {**template, "choices": [choice]}
            if usage:
                event["usage"] = usage
            return b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
    if not calls:
        choice = {"index": 0, "delta": {"content": "".join(text)}, "finish_reason": finish}
        if _repair_tool_arguments({"choices": [choice]}, body):
            calls = {i: call for i, call in enumerate(choice["delta"].get("tool_calls") or [])}
            text = []
            finish = "tool_calls"
            recovered_markup = True
        else:
            original_text = "".join(text)
            clean, recovered = _repair_textual_tool_calls(original_text, body)
            if recovered:
                text = [clean]
                calls = dict(enumerate(recovered))
                finish = "tool_calls"
                recovered_markup = True
            elif clean != original_text:
                # A pseudo-tool such as `table` was rendered as user-visible text.
                text = [clean]
                content_changed = True
            else:
                return None
    else:
        recovered_markup = False
    choice = {"index": 0, "delta": {"content": "".join(text), "tool_calls": list(calls.values())},
              "finish_reason": finish}
    if (not _repair_tool_arguments({"choices": [choice]}, body)
            and not recovered_markup and not stream_changed and not content_changed):
        return None
    for index, call in enumerate(choice["delta"].get("tool_calls") or []):
        call.setdefault("index", index)
    event = {**template, "choices": [choice]}
    if usage:
        event["usage"] = usage
    return b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"


def repair_leaked_tool_stream(lines, body):
    """Stream lines untouched unless they look like leaked tool-call markup.

    Only replies that begin with JSON tool syntax are buffered and repaired; normal
    chat replies keep streaming as before.
    """
    prefetch = []
    for line in lines:
        prefetch.append(line)
        if len(prefetch) >= 3:
            break
    markers = ('"content": "[', '"content":"[', '\\"name\\"', '"name":', '<tool_call', '"arguments"')
    suspicious = any(any(marker in str(line) for marker in markers) for line in prefetch)
    if not suspicious:
        yield from prefetch
        yield from lines
        return
    buffered = [line.encode() if isinstance(line, str) else line
                for line in prefetch]
    buffered.extend(line.encode() if isinstance(line, str) else line for line in lines)
    repaired = _guard_streamed_tool_calls(buffered, body)
    if repaired is None:
        yield from (line.decode() if isinstance(line, bytes) else line for line in buffered)
        return
    for line in repaired.decode().splitlines():
        yield line


def _flm_stream_lines(lines, model):
    """Normalize FLM's single-choice SSE terminator before tool repair or forwarding."""
    finished = False
    has_calls = False
    template = {"object": "chat.completion.chunk", "model": model}
    for line in lines:
        if not line or not line.startswith(b"data:"):
            yield line
            continue
        payload = line[5:].strip()
        if payload == b"[DONE]":
            if not finished:
                reason = "tool_calls" if has_calls else "stop"
                terminal = {**template, "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}
                yield b""
                yield b"data: " + json.dumps(terminal).encode()
                yield b""
            yield b"data: [DONE]"
            yield b""
            return
        try:
            event = json.loads(payload)
        except ValueError:
            yield line
            continue
        if isinstance(event, dict):
            for key in ("id", "model", "created"):
                if key in event:
                    template[key] = event[key]
            for choice in event.get("choices") or []:
                finished |= bool(choice.get("finish_reason"))
                has_calls |= bool((choice.get("delta") or {}).get("tool_calls"))
            if event.get("error"):
                finished = True
        yield b"data: " + payload
    if not finished:
        error = {"error": {"message": "FastFlowLM disconnected before completing the response."}}
        yield b""
        yield b"data: " + json.dumps(error).encode()
        yield b""
    yield b"data: [DONE]"
    yield b""
