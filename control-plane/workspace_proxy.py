"""Fixed loopback workspaces exposed through Ariadne's HTTPS origin."""
import http.client
import re
from urllib.parse import urlsplit


def _rewrite_workspace_url(value, prefix, origin):
    if value.startswith("/") and not value.startswith("//"):
        value = prefix.rstrip("/") + value
    return value.replace(origin.rstrip("/"), prefix.rstrip("/"))


def _rewrite_workspace_javascript_url(value, prefix, origin):
    # Vite emits root-based module URLs, and God’s Eye uses these application
    # paths for its API and static assets. Other slash-prefixed strings in
    # JavaScript can be path syntax (Cesium parses URLs this way), so leave
    # them untouched.
    workspace_paths = (
        "/@fs/", "/@id/", "/@vite/", "/api/", "/assets/", "/cesium/",
        "/data/", "/mic.svg", "/models/", "/node_modules/", "/src/", "/logo.svg",
    )
    prefix = prefix.rstrip("/")
    if value.startswith(prefix + "/"):
        return value.replace(origin.rstrip("/"), prefix)
    if value.startswith("/node_modules/.vite/deps/"):
        separator = "&" if "?" in value else "?"
        value += separator + "ariadne_proxy=1"
    if value.startswith(workspace_paths):
        value = prefix + value
    # Some God’s Eye controls build markup inside template literals, so their
    # root-relative image references are embedded inside a larger string.
    def rewrite_html_attribute(match):
        path = match.group(2)
        if path.startswith(prefix + "/"):
            return match.group(0)
        return match.group(1) + prefix + path

    value = re.sub(
        r'((?:src|href)\s*=\s*["\'])(/[^"\']*)',
        rewrite_html_attribute,
        value,
        flags=re.IGNORECASE,
    )
    return value.replace(origin.rstrip("/"), prefix)


def _rewrite_workspace_javascript(text, prefix, origin):
    """Rewrite URL strings without treating JavaScript regexes as strings."""
    output = []
    index = 0
    can_start_regex = True
    regex_prefix_words = {
        "await", "case", "delete", "do", "else", "in", "instanceof",
        "new", "of", "return", "throw", "typeof", "void", "yield",
    }
    while index < len(text):
        char = text[index]
        if char in "'\"`":
            quote = char
            end = index + 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == quote:
                    end += 1
                    break
                end += 1
            token = text[index + 1 : end - 1]
            output.extend((quote, _rewrite_workspace_javascript_url(token, prefix, origin), quote))
            index = end
            can_start_regex = False
            continue
        if char == "/" and index + 1 < len(text):
            if text[index + 1] == "/":
                end = text.find("\n", index + 2)
                if end < 0:
                    end = len(text)
                output.append(text[index:end])
                index = end
                continue
            if text[index + 1] == "*":
                end = text.find("*/", index + 2)
                end = len(text) if end < 0 else end + 2
                output.append(text[index:end])
                index = end
                continue
            if can_start_regex:
                end = index + 1
                in_character_class = False
                while end < len(text):
                    if text[end] == "\\":
                        end += 2
                        continue
                    if text[end] == "[":
                        in_character_class = True
                    elif text[end] == "]":
                        in_character_class = False
                    elif text[end] == "/" and not in_character_class:
                        end += 1
                        while end < len(text) and text[end].isalpha():
                            end += 1
                        break
                    end += 1
                output.append(text[index:end])
                index = end
                can_start_regex = False
                continue
            output.append(char)
            index += 1
            can_start_regex = True
            continue
        if char.isalpha() or char in "_$":
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            word = text[index:end]
            output.append(word)
            index = end
            can_start_regex = word in regex_prefix_words
            continue
        output.append(char)
        if not char.isspace():
            can_start_regex = char in "([{,:;=!?&|+-*%^~<>"
        index += 1
    return "".join(output)


def rewrite_workspace_text(body, prefix, origin, *, javascript=False, html=False):
    text = body.decode("utf-8")
    if javascript:
        return _rewrite_workspace_javascript(text, prefix, origin).encode("utf-8")
    if html:
        # Ariadne's fixed HTTP proxy does not tunnel Vite's HMR WebSocket.
        # Remove the optional development client from the integrated page;
        # the workspace's main module and standalone startup remain unchanged.
        text = re.sub(
            r'<script\b(?=[^>]*\bsrc=["\']/@vite/client["\'])[^>]*>\s*</script\s*>',
            "",
            text,
            flags=re.IGNORECASE,
        )
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
        # Cesium's optimized Vite dependency falls back to its module URL when
        # its compile-time base URL is unavailable. Its Assets/Workers/etc.
        # therefore appear beside .vite/deps even though vite-plugin-cesium
        # serves those files from /cesium/.
        cesium_asset_prefix = "/node_modules/.vite/deps/"
        cesium_asset_roots = ("Assets/", "ThirdParty/", "Widgets/", "Workers/")
        if path.startswith(cesium_asset_prefix):
            tail = path[len(cesium_asset_prefix):]
            if tail.startswith(cesium_asset_roots):
                path = "/cesium/" + tail
        # The response body is rewritten below, so an upstream Vite validator
        # can describe different bytes than the browser cached. Always fetch
        # the current workspace representation instead of accepting a stale
        # 304 for a transformed asset.
        headers = {k: v for k, v in handler.headers.items() if k.lower() not in {
            "host", "connection", "accept-encoding", "transfer-encoding",
            "if-none-match", "if-modified-since",
        }}
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Prefix"] = prefix.rstrip("/")
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=30)
        try:
            connection.request(handler.command, path, body=body, headers=headers)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "application/octet-stream")
            if path.endswith((".json", ".png", ".jpg", ".jpeg", ".webp", ".ktx", ".wasm")) and "text/html" in content_type:
                handler.log_message(
                    "workspace asset returned HTML: %s -> %s (%s)",
                    handler.path,
                    path,
                    content_type,
                )
            textual = any(kind in content_type for kind in ("text/html", "javascript", "text/css", "application/json"))
            rewritten = rewrite_workspace_text(
                response.read(),
                prefix,
                origin,
                javascript="javascript" in content_type,
                html="text/html" in content_type,
            ) if textual else None
            if (
                rewritten is not None
                and "text/html" in content_type
                and handler.command == "GET"
                and prefix.rstrip("/") == "/workspaces/video"
                and path in {"/", "/index.html"}
            ):
                page = rewritten.decode("utf-8")
                lifecycle = """<script>(function(){
  const workload="renderer";
  const key="ariadne.media-workspace.renderer";
  let sessionId=sessionStorage.getItem(key);
  if(!/^[a-f0-9]{32}$/.test(sessionId||"")){
    sessionId=crypto.randomUUID().replaceAll("-","");
    sessionStorage.setItem(key,sessionId);
  }
  const send=(action)=>fetch("/api/media/workspace",{
    method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({workload,action,session_id:sessionId}),cache:"no-store"
  }).catch(()=>{});
  void send("open");
  const heartbeat=setInterval(()=>void send("heartbeat"),5000);
  addEventListener("pagehide",()=>{
    clearInterval(heartbeat);
    navigator.sendBeacon("/api/media/workspace",new Blob([
      JSON.stringify({workload,action:"close",session_id:sessionId})
    ],{type:"application/json"}));
  },{once:true});
})();</script>"""
                page = re.sub(r"</body\s*>", lambda match: lifecycle + match.group(0), page, count=1, flags=re.IGNORECASE)
                rewritten = page.encode("utf-8")
            handler.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() in {"connection", "transfer-encoding", "content-encoding"} or (
                    textual and name.lower() in {"content-length", "etag", "last-modified"}
                ) or (rewritten is not None and name.lower() == "cache-control"):
                    continue
                if name.lower() == "location":
                    value = prefix.rstrip("/") + value[len(origin.rstrip("/")):] if value.startswith(origin.rstrip("/")) else prefix + value.lstrip("/") if value.startswith("/") else value
                handler.send_header(name, value)
            if rewritten is not None:
                handler.send_header("Cache-Control", "no-store")
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
