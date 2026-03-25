#!/usr/bin/env python3
import ast
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

APP_NAME = "fix-invalid-json-app"
APP_VERSION = "1.0.0"
SUPPORT_EMAIL = "sidcraigau@gmail.com"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
MCP_ERROR_MESSAGE = "Input must be a valid JSON-like string"

TOOL_DEFINITION = {
    "name": "fix_invalid_json",
    "description": "Fix malformed or invalid JSON into valid usable JSON.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "A JSON-like string that may contain syntax errors and needs repair.",
            }
        },
        "required": ["input"],
        "additionalProperties": False,
    },
}


class RepairError(Exception):
    pass


def _remove_trailing_commas(text):
    return re.sub(r",\s*(?=[}\]])", "", text)


def _balance_braces_brackets(text):
    stack = []
    pairs = {"{": "}", "[": "]"}
    closing = set(pairs.values())
    in_string = False
    quote = ""
    escape = False

    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
            continue

        if ch in ('"', "'"):
            in_string = True
            quote = ch
            continue

        if ch in pairs:
            stack.append(ch)
        elif ch in closing:
            if not stack:
                raise RepairError(MCP_ERROR_MESSAGE)
            opener = stack.pop()
            if pairs[opener] != ch:
                raise RepairError(MCP_ERROR_MESSAGE)

    while stack:
        text += pairs[stack.pop()]
    return text


def repair_json_like(input_text):
    if not isinstance(input_text, str):
        raise RepairError(MCP_ERROR_MESSAGE)

    text = input_text.strip()
    if not text:
        raise RepairError(MCP_ERROR_MESSAGE)

    for candidate in (text, _remove_trailing_commas(text), _remove_trailing_commas(_balance_braces_brackets(text))):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    normalized = _remove_trailing_commas(_balance_braces_brackets(text))
    try:
        value = ast.literal_eval(normalized)
    except (ValueError, SyntaxError):
        raise RepairError(MCP_ERROR_MESSAGE)

    try:
        json.dumps(value)
    except (TypeError, ValueError):
        raise RepairError(MCP_ERROR_MESSAGE)

    return value


def make_mcp_error(request_id, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": message}}


class AppHandler(BaseHTTPRequestHandler):
    server_version = "FixInvalidJSON/1.0"

    def _send(self, status, payload, content_type="application/json"):
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/privacy":
            return self._send(200, "no data stored", "text/plain")
        if self.path == "/terms":
            return self._send(200, "Use only with valid input", "text/plain")
        if self.path == "/support":
            return self._send(200, f"support: {SUPPORT_EMAIL}", "text/plain")
        if self.path == "/.well-known/openai-apps-challenge":
            challenge = os.environ.get("OPENAI_APPS_CHALLENGE", "PLACEHOLDER")
            return self._send(200, challenge, "text/plain")
        if self.path == "/mcp":
            return self._send(200, {"name": APP_NAME, "version": APP_VERSION, "tools": [TOOL_DEFINITION]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mcp":
            return self._send(404, {"error": "not found"})

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send(400, make_mcp_error(None, "Invalid JSON-RPC request"))

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method == "notifications/initialized":
            self.send_response(204)
            self.end_headers()
            return

        if method == "initialize":
            protocol_version = params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
            result = {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": APP_NAME, "version": APP_VERSION},
            }
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result})

        if method == "ping":
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": {}})

        if method == "tools/list":
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [TOOL_DEFINITION]}})

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments")
            if tool_name != "fix_invalid_json":
                return self._send(200, make_mcp_error(request_id, "Unknown tool"))
            if not isinstance(arguments, dict) or set(arguments.keys()) != {"input"} or not isinstance(arguments.get("input"), str) or not arguments.get("input"):
                return self._send(200, make_mcp_error(request_id, MCP_ERROR_MESSAGE))
            try:
                repaired = repair_json_like(arguments["input"])
            except RepairError as exc:
                return self._send(200, make_mcp_error(request_id, str(exc)))

            result = {
                "content": [{"type": "text", "text": "Fixed invalid JSON"}],
                "structuredContent": {"input": arguments["input"], "output": repaired},
            }
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result})

        return self._send(200, make_mcp_error(request_id, "Method not supported"))

    def log_message(self, fmt, *args):
        return


def run_server(host="0.0.0.0", port=None):
    listen_port = int(os.environ.get("PORT", "8000")) if port is None else int(port)
    server = ThreadingHTTPServer((host, listen_port), AppHandler)
    server.serve_forever()


def _post_json(url, payload):
    req = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _get(url):
    with urlopen(url, timeout=5) as resp:
        ct = resp.headers.get("Content-Type", "")
        data = resp.read().decode("utf-8")
        return resp.status, ct, data


def run_self_tests():
    source = open(__file__, "r", encoding="utf-8").read()
    assert "0.0.0.0" in source, "Static check failed: host binding missing"
    assert 'os.environ.get("PORT"' in source, "Static check failed: PORT env missing"

    test_server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
    port = test_server.server_address[1]
    thread = threading.Thread(target=test_server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"

    try:
        status, _, body = _get(base + "/health")
        assert status == 200, "Health status code mismatch"
        assert json.loads(body) == {"status": "ok"}, "Health body mismatch"

        status, initialize_resp = _post_json(
            base + "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-01-01"}},
        )
        assert status == 200, "Initialize status code mismatch"
        assert initialize_resp["result"]["protocolVersion"] == "2025-01-01", "Protocol version mismatch"
        assert initialize_resp["result"]["serverInfo"] == {"name": APP_NAME, "version": APP_VERSION}, "Server info mismatch"

        status, tools_resp = _post_json(base + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert status == 200, "tools/list status code mismatch"
        tools = tools_resp["result"]["tools"]
        assert len(tools) == 1 and tools[0]["name"] == "fix_invalid_json", "tools/list payload mismatch"

        status, call_resp_1 = _post_json(
            base + "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "fix_invalid_json", "arguments": {"input": "{'a':1,}"}},
            },
        )
        assert status == 200, "tools/call test1 status mismatch"
        assert call_resp_1["result"]["structuredContent"] == {"input": "{'a':1,}", "output": {"a": 1}}, "tools/call test1 mismatch"

        status, call_resp_2 = _post_json(
            base + "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "fix_invalid_json", "arguments": {"input": '{"a":1,"b":2,}' }},
            },
        )
        assert status == 200, "tools/call test2 status mismatch"
        assert call_resp_2["result"]["structuredContent"] == {
            "input": '{"a":1,"b":2,}',
            "output": {"a": 1, "b": 2},
        }, "tools/call test2 mismatch"

        status, call_resp_3 = _post_json(
            base + "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "fix_invalid_json", "arguments": {"input": "hello world"}},
            },
        )
        assert status == 200, "tools/call test3 status mismatch"
        assert call_resp_3["error"]["message"] == MCP_ERROR_MESSAGE, "tools/call test3 error mismatch"

        print("All self-tests passed")
    finally:
        test_server.shutdown()
        test_server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    if "--self-test" in os.sys.argv:
        run_self_tests()
    else:
        run_server()
