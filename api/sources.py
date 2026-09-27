import json
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

DATABASE_URL = "https://fram-and-go-default-rtdb.asia-southeast1.firebasedatabase.app/cholchetv/playlists.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            request = urllib.request.Request(DATABASE_URL, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=10) as response:
                playlists = json.loads(response.read())
            sources = [str(i + 1) for i, u in enumerate(playlists) if u]
            if not sources:
                raise ValueError("No sources")
        except (ValueError, urllib.error.URLError, json.JSONDecodeError):
            self.send_error(502, "Unable to load sources")
            return

        body = json.dumps({"sources": sources}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, s-maxage=60")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
