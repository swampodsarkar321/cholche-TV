import urllib.request, urllib.parse, json, re, time, base64, threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
DB = "https://fram-and-go-default-rtdb.asia-southeast1.firebasedatabase.app"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Referer": "https://google.com/"}
def fb_get(path):
    url = f"{DB}/{path}.json"
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read())
def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read()
def get_retry(url, tries=3):
    # live TV segments often reset mid-transfer -> retry transient failures
    err = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
        except Exception as e:
            err = e
            time.sleep(0.5 * (i + 1))
    raise err
def base_id_of(url):
    m = re.search(r"go\.skym3u\.dev/([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else None
def fresh_url(bid):
    # skym3u verify flow: token = b64(now_ms + "_skym3u_pass_" + id)
    raw = f"{int(time.time()*1000)}_skym3u_pass_{bid}"
    tok = base64.b64encode(raw.encode()).decode()
    vurl = f"https://go.skym3u.dev/verify?id={bid}&token={urllib.parse.quote(tok)}"
    try:
        data = json.loads(get(vurl))
        if isinstance(data, dict) and data.get("status") == "success" and str(data.get("data", "")).startswith("http"):
            return data["data"]
    except Exception:
        pass
    return None
# fetch playlist URLs from Firebase (store base IDs so tokens auto-refresh)
URLS = {}
IDS = {}
CACHE = {}  # path -> (fresh_url, timestamp)
PL_CACHE = {}   # path -> (bytes, timestamp)
PL_TTL = 300    # serve instantly if fresher than this
def sync_playlists():
    # re-read admin config: add new sources, drop removed ones (auto-sync, no restart)
    global URLS, IDS
    try:
        cfg = fb_get("cholchetv/playlists")
        new_urls = {f"/pl{i+1}.m3u": u for i, u in enumerate(cfg) if u}
    except Exception:
        return
    for p in list(URLS):
        if p not in new_urls:
            URLS.pop(p, None); IDS.pop(p, None); PL_CACHE.pop(p, None); CACHE.pop(p, None)
    for p, u in new_urls.items():
        if URLS.get(p) != u:
            URLS[p] = u; IDS[p] = base_id_of(u)
            try:
                PL_CACHE[p] = (fetch_playlist(p), time.time())
            except Exception:
                pass
sync_playlists()
def resolve_url(path):
    bid = IDS.get(path)
    now = time.time()
    if path in CACHE and now - CACHE[path][1] < 600:
        return CACHE[path][0]
    if bid:
        f = fresh_url(bid)
        if f:
            URLS[path] = f
            CACHE[path] = (f, now)
            return f
    return URLS.get(path)
def fetch_playlist(path):
    url = resolve_url(path)
    data = get(url)
    if b"#EXT" not in data:
        # token expired mid-cache -> force refresh once
        bid = IDS.get(path)
        if bid:
            f = fresh_url(bid)
            if f and f != url:
                URLS[path] = f
                CACHE[path] = (f, time.time())
                data = get(f)
    if b"#EXT" not in data:
        raise ValueError("upstream playlist expired")
    return data
# ---- preload: all channels ready in memory before first visitor ----
def get_cached_playlist(path):
    now = time.time()
    if path in PL_CACHE and now - PL_CACHE[path][1] < PL_TTL:
        return PL_CACHE[path][0]
    try:
        data = fetch_playlist(path)
        PL_CACHE[path] = (data, now)
        return data
    except Exception:
        if path in PL_CACHE:  # upstream hiccup -> serve last good copy, never blank UI
            return PL_CACHE[path][0]
        raise
def refresh_loop():
    while True:
        time.sleep(120)
        sync_playlists()  # admin changes auto-sync, no restart needed
        with ThreadPoolExecutor(max_workers=4) as _ex:
            list(_ex.map(_warm_one, list(URLS)))
def _warm_one(p):
    try:
        PL_CACHE[p] = (fetch_playlist(p), time.time())
    except Exception:
        pass
with ThreadPoolExecutor(max_workers=4) as _ex0:  # parallel warm: first load instant
    list(_ex0.map(_warm_one, list(URLS)))
threading.Thread(target=refresh_loop, daemon=True).start()
# (server auto health-check removed — watch page play-fail auto-reports broken channels to Firebase)
def proxify_m3u8(text, base):    # rewrite segment/key URIs to absolute + routed via proxy (keeps headers + tokens working)
    import re as _re
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith('#'):
            absu = urllib.parse.urljoin(base, s)
            out.append('/api/stream?u=' + urllib.parse.quote(absu, safe=''))
        elif s.startswith('#') and 'URI="' in s:
            def _rep(m):
                absu = urllib.parse.urljoin(base, m.group(1))
                return 'URI="/api/stream?u=' + urllib.parse.quote(absu, safe='') + '"'
            out.append(_re.sub(r'URI="([^"]+)"', _rep, s))
        else:
            out.append(line)
    return ('\n'.join(out) + '\n').encode('utf-8')
class H(SimpleHTTPRequestHandler):
    def end_headers(self):
        # never cache html: guarantees users always get the latest player code
        if self.path.split("?")[0].endswith((".html", ".m3u")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()
    def send_bin(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        if path == "/api/stream":
            q = urllib.parse.parse_qs(p.query).get("u", [""])[0]
            pr = urllib.parse.urlparse(q)
            if pr.scheme not in ("http", "https") or not pr.hostname:
                self.send_error(400, "Invalid stream URL")
                return
            try:
                data = get_retry(q)
            except Exception:
                self.send_response(502); self.end_headers(); return
            if ".m3u8" in pr.path or ".m3u" in pr.path or q and ".m3u8" in urllib.parse.urlparse(q).path:
                try:
                    return self.send_bin(proxify_m3u8(data.decode("utf-8", "ignore"), q), "application/vnd.apple.mpegurl")
                except Exception:
                    pass
            ctype = "video/MP2T" if (pr.path.endswith(".ts") or (q and urllib.parse.urlparse(q).path.endswith(".ts"))) else "application/octet-stream"
            try:
                return self.send_bin(data, ctype)
            except Exception:
                self.send_response(502); self.end_headers(); return
        if path == "/api/sources":
            try:
                nums = sorted(p[3:-4] for p in URLS if p.startswith("/pl") and p.endswith(".m3u"))
                body = json.dumps({"sources": nums}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_response(502); self.end_headers()
            return
        if path == "/api/playlist":
            source = urllib.parse.parse_qs(p.query).get("source", [""])[0]
            path = f"/pl{source}.m3u"
            if path not in URLS:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Playlist unavailable")
                return
        if path in URLS:
            try:
                return self.send_bin(get_cached_playlist(path), "audio/x-mpegurl")
            except Exception:
                self.send_response(502); self.end_headers(); return
        return super().do_GET()
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", 8090), H).serve_forever()
