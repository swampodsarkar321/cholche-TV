import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

DATABASE_URL = "https://fram-and-go-default-rtdb.asia-southeast1.firebasedatabase.app/cholchetv/playlists.json"
PLAYLIST_HOSTS = ("go.skym3u.dev", "m3u.devm3u.top", "raw.githubusercontent.com")
MAX_PLAYLIST_SIZE = 2 * 1024 * 1024
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://google.com/",
}


def fetch(url, limit=MAX_PLAYLIST_SIZE):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Playlist is too large")
    return data


def fresh_url(bid):
    raw = f"{int(time.time() * 1000)}_skym3u_pass_{bid}"
    tok = base64.b64encode(raw.encode()).decode()
    vurl = f"https://go.skym3u.dev/verify?id={bid}&token={urllib.parse.quote(tok)}"
    try:
        data = json.loads(fetch(vurl))
        if isinstance(data, dict) and data.get("status") == "success" and str(data.get("data", "")).startswith("http"):
            return data["data"]
    except (ValueError, urllib.error.URLError, json.JSONDecodeError):
        pass
    return None


def resolve_playlist_url(stored_url):
    m = re.search(r"go\.skym3u\.dev/([A-Za-z0-9]+)", stored_url or "")
    if m:
        fresh = fresh_url(m.group(1))
        if fresh and is_allowed_playlist(fresh):
            return fresh
    return stored_url


def is_allowed_playlist(url):
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in PLAYLIST_HOSTS
        and parsed.port in (None, 443)
        and not parsed.username
        and not parsed.password
    )


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        source = query.get("source", [""])[0]
        if not source.isdigit() or int(source) < 1:
            self.send_error(400, "Invalid playlist source")
            return

        try:
            playlists = json.loads(fetch(DATABASE_URL))
            playlist_url = playlists[int(source) - 1]
            if not isinstance(playlist_url, str) or not is_allowed_playlist(playlist_url):
                self.send_error(404, "Playlist unavailable")
                return
            playlist_url = resolve_playlist_url(playlist_url)
            playlist = fetch(playlist_url)
            if b"#EXT" not in playlist:
                # stored token expired and verify returned stale URL -> retry once
                retry = resolve_playlist_url(playlists[int(source) - 1])
                if retry != playlist_url:
                    playlist = fetch(retry)
        except (IndexError, ValueError, urllib.error.URLError, json.JSONDecodeError):
            self.send_error(502, "Unable to load playlist")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-mpegURL; charset=utf-8")
        self.send_header("Cache-Control", "public, s-maxage=300, stale-while-revalidate=600")
        self.send_header("Content-Length", str(len(playlist)))
        self.end_headers()
        self.wfile.write(playlist)
