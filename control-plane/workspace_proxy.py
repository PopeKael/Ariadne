"""Fixed loopback workspaces exposed through Ariadne's HTTPS origin."""
import http.client
import re
from urllib.parse import urlsplit


def rewrite_workspace_text(body, prefix, origin):
    text = body.decode("utf-8")
    # The renderer and Vite workspace use root-relative assets/API imports.
    text = re.sub(r"([\"'`])/(?!/)", lambda match: match[1] + prefix, text)
    text = re.sub(r"url\(/(?!/)", "url(" + prefix, text)
    text = text.replace(origin.rstrip("/"), prefix.rstrip("/"))
    return text.encode("utf-8")


def proxy_workspace(handler, prefix, origin):
    target = urlsplit(origin)
    if target.hostname not in {"127.0.0.1", "localhost", "::1"} or target.scheme != "http":
        handler.send_json({"ok": False, "message": "Workspace backend must be loopback."}, 502)
        return
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if length < 0 or length > 8_000_000 or handler.headers.get("Transfer-Encoding"):
            raise ValueError("Invalid workspace request length.")
        body = handler.rfile.read(length) if length else None
        path = "/" + handler.path[len(prefix):]
        headers = {k: v for k, v in handler.headers.items() if k.lower() not in {"host", "connection", "accept-encoding", "transfer-encoding"}}
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Prefix"] = prefix.rstrip("/")
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=30)
        try:
            connection.request(handler.command, path, body=body, headers=headers)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "application/octet-stream")
            textual = any(kind in content_type for kind in ("text/html", "javascript", "text/css", "application/json"))
            rewritten = rewrite_workspace_text(response.read(), prefix, origin) if textual else None
            handler.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() in {"connection", "transfer-encoding", "content-encoding"} or (textual and name.lower() == "content-length"):
                    continue
                if name.lower() == "location":
                    value = prefix.rstrip("/") + value[len(origin.rstrip("/")):] if value.startswith(origin.rstrip("/")) else prefix + value.lstrip("/") if value.startswith("/") else value
                handler.send_header(name, value)
            if rewritten is not None:
                handler.send_header("Content-Length", str(len(rewritten)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            if rewritten is not None:
                handler.wfile.write(rewritten)
            else:
                while chunk := response.read1(65536):
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
            handler.close_connection = True
        finally:
            connection.close()
    except (OSError, ValueError, http.client.HTTPException):
        handler.send_json({"ok": False, "message": "This workspace is not running. Start it from Create or Tools."}, 503)
