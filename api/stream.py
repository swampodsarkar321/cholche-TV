import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

MAX_M3U8_SIZE = 2 * 1024 * 1024
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://google.com/",
}
BLOCKED_NAMES = {"localhost"}


def _blocked(url):
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return True
        host = (parsed.hostname or "").lower()
        if not host or host in BLOCKED_NAMES:
            return True
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
        except ValueError:
            pass
        return False
    except Exception:
        return True


def fetch(url, limit=MAX_M3U8_SIZE):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=10) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Too large")
    return data


def absolutize_m3u8(text, base):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(urllib.parse.urljoin(base, s))
        elif s.startswith("#") and 'URI="' in s:
            def _rep(m):
                return 'URI="' + urllib.parse.urljoin(base, m.group(1)) + '"'
            out.append(re.sub(r'URI="([^"]+)"', _rep, s))
        else:
            out.append(line)
    return ("\n".join(out) + "\n").encode("utf-8")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        target = query.get("u", [""])[0]
        if not target or _blocked(target):
            self.send_error(400, "Invalid stream URL")
            return

        path = urllib.parse.urlparse(target).path
        if ".m3u8" not in path:
            # segments / keys: redirect, browser fetches direct
            # (upstream sends CORS *, so no proxy bandwidth needed)
            try:
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except Exception:
                pass
            return

        try:
            data = fetch(target)
            text = data.decode("utf-8", "ignore")
            if "#EXT" not in text:
                raise ValueError("Not a playlist")
            body = absolutize_m3u8(text, target)
        except (ValueError, urllib.error.URLError):
            self.send_error(502, "Unable to load stream")
            return

        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegURL; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, s-maxage=10, stale-while-revalidate=30")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass
