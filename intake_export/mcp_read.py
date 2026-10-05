"""Fixed-endpoint, credential-free client of the existing read-only Gorgias MCP."""
import json
import urllib.request

ENDPOINT = "http://127.0.0.1:8079/mcp"
ALLOWED_TOOLS = {"list_inbox_tickets", "get_ticket", "get_ticket_messages"}
MAX_RESPONSE = 16 * 1024 * 1024


class ExportError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ReadClient:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.session, self.sequence = None, 0

    def __enter__(self):
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "offline-export-reader", "version": "1"}})
        self.rpc("notifications/initialized", {}, notification=True)
        definitions = self.rpc("tools/list", {}).get("tools", [])
        readonly = {t["name"] for t in definitions if (t.get("annotations") or {}).get("readOnlyHint") is True}
        if not ALLOWED_TOOLS <= readonly:
            raise ExportError("Required read-only MCP tools are unavailable; no fallback to provider credentials.")
        return self

    def __exit__(self, *args):
        if self.session:
            try:
                # This deletes only our MCP transport session, never a provider record.
                request = urllib.request.Request(ENDPOINT, method="DELETE", headers={"Mcp-Session-Id": self.session})
                with self.opener.open(request, timeout=3):
                    pass
            except Exception:
                pass

    def rpc(self, method, params, notification=False):
        self.sequence += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.sequence
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2024-11-05"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=45) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                if notification:
                    return {}
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    chunks, size, result = [], 0, None
                    for line in response:
                        size += len(line)
                        if size > MAX_RESPONSE:
                            raise ExportError("MCP response exceeds the export size limit.")
                        if line.startswith(b"data:"):
                            chunks.append(line[5:].strip())
                        elif not line.strip() and chunks:
                            event = json.loads(b"\n".join(chunks))
                            chunks = []
                            if event.get("id") == self.sequence:
                                result = event
                                break
                else:
                    raw = response.read(MAX_RESPONSE + 1)
                    if len(raw) > MAX_RESPONSE:
                        raise ExportError("MCP response exceeds the export size limit.")
                    result = json.loads(raw)
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError("Read-only MCP request failed; no provider response content was logged.") from exc
        if not result or result.get("error"):
            raise ExportError("Read-only MCP returned an unsuccessful response.")
        return result.get("result", {})

    def call(self, name, arguments):
        if name not in ALLOWED_TOOLS:
            raise ExportError("Only the three export read tools are allowed.")
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise ExportError("Read-only MCP tool failed.")
        data = result.get("structuredContent")
        if not isinstance(data, dict):
            try:
                data = next(json.loads(c["text"]) for c in result.get("content", []) if c.get("type") == "text")
            except (StopIteration, ValueError, KeyError) as exc:
                raise ExportError("Read-only MCP result is malformed.") from exc
        if not isinstance(data, dict) or data.get("error"):
            raise ExportError("Gorgias read failed; no response content was logged.")
        return data
