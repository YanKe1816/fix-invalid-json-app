# fix-invalid-json-app

A deterministic MCP task app that repairs malformed JSON-like input into valid JSON when safe and parseable.

## Deploy on Render
- **Build Command:** *(leave empty)*
- **Start Command:** `python server.py`

## Public endpoints
- `GET /health` → `{"status":"ok"}`
- `GET /privacy` → full privacy policy text (plain text)
- `GET /terms` → full terms text (plain text)
- `GET /support` → support contact text including `sidcraigau@gmail.com`
- `GET /.well-known/openai-apps-challenge` → `OPENAI_APPS_CHALLENGE` or `PLACEHOLDER`

## MCP endpoint
- `GET /mcp` for inspection
- `POST /mcp` supports `initialize`, `notifications/initialized`, `tools/list`, `tools/call`, `ping`
- Tool description: `Use this tool when JSON input is malformed, contains syntax errors, or cannot be parsed, and needs to be repaired into valid JSON.`

## Example tools/call payload
```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "tools/call",
  "params": {
    "name": "fix_invalid_json",
    "arguments": {
      "input": "{'a':1,}"
    }
  }
}
```

## Example success response
```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "result": {
    "content": [],
    "structuredContent": {
      "input": "{'a':1,}",
      "output": {
        "a": 1
      }
    }
  }
}
```

## Example error response (invalid plain text)
```json
{
  "jsonrpc": "2.0",
  "id": 11,
  "error": {
    "code": -32000,
    "message": "Input must be a valid JSON-like string"
  }
}
```
