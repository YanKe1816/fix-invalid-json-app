# fix-invalid-json-app

A deterministic MCP task app that repairs malformed JSON-like strings into valid JSON values when safely possible.

## Deploy on Render
- **Build Command:** *(leave empty)*
- **Start Command:** `python server.py`

## Public endpoints
- `GET /health` → `{"status":"ok"}`
- `GET /privacy` → `no data stored`
- `GET /terms` → `Use only with valid input`
- `GET /support` → support contact including `sidcraigau@gmail.com`
- `GET /.well-known/openai-apps-challenge` → challenge token or `PLACEHOLDER`

## MCP endpoint
- `GET /mcp` for inspection
- `POST /mcp` for JSON-RPC (`initialize`, `notifications/initialized`, `tools/list`, `tools/call`, `ping`)

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
    "content": [
      {
        "type": "text",
        "text": "Fixed invalid JSON"
      }
    ],
    "structuredContent": {
      "input": "{'a':1,}",
      "output": {
        "a": 1
      }
    }
  }
}
```

## Example error response
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
