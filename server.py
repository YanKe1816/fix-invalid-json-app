#!/usr/bin/env python3
import ast
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

APP_NAME = "fix-invalid-json-app"
APP_VERSION = "1.0.0"
SUPPORT_EMAIL = "sidcraigau@gmail.com"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
MCP_ERROR_MESSAGE = "Input must be a valid JSON-like string"
TOOL_DESCRIPTION = (
    "Use this tool when JSON input is malformed, contains syntax errors, "
    "or cannot be parsed, and needs to be repaired into valid JSON."
)
PRIVACY_TEXT = (
    "This service processes user-provided JSON input solely for the purpose of repairing malformed JSON syntax into valid JSON.\n\n"
    "We do not store, log, or share user data. All processing is performed in real time and discarded immediately after completion.\n\n"
    "No personal data is retained.\n\n"
    f"For questions or support, contact: {SUPPORT_EMAIL}"
)
TERMS_TEXT = (
    "This service is provided as-is for JSON repair purposes.\n\n"
    "We do not guarantee that every malformed input can be repaired safely or correctly in all edge cases.\n\n"
    "Users are responsible for reviewing and validating outputs before use.\n\n"
    "This service should not be used in critical systems without independent verification."
)

TOOL_DEFINITION = {
    "name": "fix_invalid_json",
    "description": TOOL_DESCRIPTION,
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
    openers = {"{": "}", "[": "]"}
    closers = set(openers.values())
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

        if ch in openers:
            stack.append(ch)
        elif ch in closers:
            if not stack:
                raise RepairError(MCP_ERROR_MESSAGE)
            opener = stack.pop()
            if openers[opener] != ch:
                raise RepairError(MCP_ERROR_MESSAGE)

    while stack:
        text += openers[stack.pop()]
    return text


def repair_json_like(input_text):
    if not isinstance(input_text, str):
        raise RepairError(MCP_ERROR_MESSAGE)

    text = input_text.strip()
    if not text:
        raise RepairError(MCP_ERROR_MESSAGE)

    candidates = [text]
    no_trailing_commas = _remove_trailing_commas(text)
    if no_trailing_commas != text:
        candidates.append(no_trailing_commas)

    try:
        balanced = _balance_braces_brackets(no_trailing_commas)
        if balanced not in candidates:
            candidates.append(balanced)
    except RepairError:
        balanced = None

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    normalized = balanced if balanced is not None else no_trailing_commas
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
        if isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/privacy":
            return self._send(200, PRIVACY_TEXT, "text/plain; charset=utf-8")
        if self.path == "/terms":
            return self._send(200, TERMS_TEXT, "text/plain; charset=utf-8")
        if self.path == "/support":
            return self._send(200, f"Support: {SUPPORT_EMAIL}", "text/plain; charset=utf-8")
        if self.path == "/.well-known/openai-apps-challenge":
            return self._send(200, os.environ.get("OPENAI_APPS_CHALLENGE", "PLACEHOLDER"), "text/plain")
        if self.path == "/mcp":
            return self._send(200, {"name": APP_NAME, "version": APP_VERSION, "tools": [TOOL_DEFINITION]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mcp":
            return self._send(404, {"error": "not found"})

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            rpc_request = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send(400, make_mcp_error(None, "Invalid JSON-RPC request"))

        request_id = rpc_request.get("id")
        method = rpc_request.get("method")
        params = rpc_request.get("params", {})

        if method == "notifications/initialized":
            self.send_response(204)
            self.end_headers()
            return

        if method == "initialize":
            protocol_version = params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
            return self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": protocol_version,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": APP_NAME, "version": APP_VERSION},
                    },
                },
            )

        if method == "ping":
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": {}})

        if method == "tools/list":
            return self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [TOOL_DEFINITION]}})

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments")
            if tool_name != "fix_invalid_json":
                return self._send(200, make_mcp_error(request_id, "Unknown tool"))
            if (
                not isinstance(arguments, dict)
                or set(arguments.keys()) != {"input"}
                or not isinstance(arguments.get("input"), str)
                or not arguments.get("input").strip()
            ):
                return self._send(200, make_mcp_error(request_id, MCP_ERROR_MESSAGE))

            try:
                repaired_output = repair_json_like(arguments["input"])
            except RepairError as exc:
                return self._send(200, make_mcp_error(request_id, str(exc)))

            return self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [],
                        "structuredContent": {"input": arguments["input"], "output": repaired_output},
                    },
                },
            )

        return self._send(200, make_mcp_error(request_id, "Method not supported"))

    def log_message(self, fmt, *args):
        return


def run_server(host="0.0.0.0", port=None):
    listen_port = int(os.environ.get("PORT", "8000")) if port is None else int(port)
    server = ThreadingHTTPServer((host, listen_port), AppHandler)
    server.serve_forever()


def _post_json(url, payload):
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _get(url):
    with urlopen(url, timeout=5) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")


def run_self_tests():
    source = open(__file__, "r", encoding="utf-8").read()
    assert "0.0.0.0" in source, "Static check failed: host binding missing"
    assert 'os.environ.get("PORT"' in source, "Static check failed: PORT env missing"
    assert "This service processes user-provided JSON input solely for the purpose of repairing malformed JSON syntax into valid JSON." in source, "Static check failed: privacy text missing"
    assert "This service is provided as-is for JSON repair purposes." in source, "Static check failed: terms text missing"
    assert '"content": []' in source, "Static check failed: content[] logic missing"

    test_server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
    port = test_server.server_address[1]
    server_thread = threading.Thread(target=test_server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.05)
    base_url = f"http://127.0.0.1:{port}"

    try:
        status, _, body = _get(base_url + "/health")
        assert status == 200, "Health status code mismatch"
        assert json.loads(body) == {"status": "ok"}, "Health response mismatch"

        status, privacy_ct, privacy_body = _get(base_url + "/privacy")
        assert status == 200, "Privacy status code mismatch"
        assert privacy_ct == "text/plain; charset=utf-8", "Privacy content-type mismatch"
        assert privacy_body == PRIVACY_TEXT, "Privacy body mismatch"

        status, terms_ct, terms_body = _get(base_url + "/terms")
        assert status == 200, "Terms status code mismatch"
        assert terms_ct == "text/plain; charset=utf-8", "Terms content-type mismatch"
        assert terms_body == TERMS_TEXT, "Terms body mismatch"

        status, initialize_resp = _post_json(
            base_url + "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-01-01"}},
        )
        assert status == 200, "Initialize status code mismatch"
        assert initialize_resp["result"]["protocolVersion"] == "2025-01-01", "Protocol version mismatch"
        assert initialize_resp["result"]["serverInfo"] == {"name": APP_NAME, "version": APP_VERSION}, "Server info mismatch"

        status, tools_resp = _post_json(base_url + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert status == 200, "tools/list status code mismatch"
        tools = tools_resp["result"]["tools"]
        assert len(tools) == 1, "tools/list tool count mismatch"
        assert tools[0]["name"] == "fix_invalid_json", "tools/list name mismatch"
        assert tools[0]["description"] == TOOL_DESCRIPTION, "tools/list description mismatch"

        status, call_resp_1 = _post_json(
            base_url + "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "fix_invalid_json", "arguments": {"input": "{'a':1,}"}},
            },
        )
        assert status == 200, "tools/call test1 status mismatch"
        assert call_resp_1["result"]["content"] == [], "tools/call test1 content mismatch"
        assert call_resp_1["result"]["structuredContent"] == {"input": "{'a':1,}", "output": {"a": 1}}, "tools/call test1 structuredContent mismatch"

        status, call_resp_2 = _post_json(
            base_url + "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "fix_invalid_json", "arguments": {"input": '{"a":1,"b":2,}' }},
            },
        )
        assert status == 200, "tools/call test2 status mismatch"
        assert call_resp_2["result"]["content"] == [], "tools/call test2 content mismatch"
        assert call_resp_2["result"]["structuredContent"] == {
            "input": '{"a":1,"b":2,}',
            "output": {"a": 1, "b": 2},
        }, "tools/call test2 structuredContent mismatch"

        status, call_resp_3 = _post_json(
            base_url + "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "fix_invalid_json", "arguments": {"input": "hello world"}},
            },
        )
        assert status == 200, "tools/call test3 status mismatch"
        assert call_resp_3["error"]["message"] == MCP_ERROR_MESSAGE, "tools/call test3 error mismatch"

        status, call_resp_4 = _post_json(
            base_url + "/mcp",
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "fix_invalid_json", "arguments": {"input": "   "}},
            },
        )
        assert status == 200, "tools/call test4 status mismatch"
        assert call_resp_4["error"]["message"] == MCP_ERROR_MESSAGE, "tools/call test4 error mismatch"

        print("All self-tests passed")
    finally:
        test_server.shutdown()
        test_server.server_close()
        server_thread.join(timeout=2)


if __name__ == "__main__":
    if "--self-test" in os.sys.argv:
        run_self_tests()
    else:
        run_server()
