# -*- coding: utf-8 -*-
"""
Al Yasmeen menu server  ·  static files + admin API (no extra installs needed)

  /                 -> index.html  (the menu)
  /admin.html       -> admin dashboard
  GET  /api/menu    -> menu.json
  POST /api/menu    -> save menu.json          (header x-admin-pass)
  POST /api/login   -> check password
  POST /api/upload  -> save image to uploads/   (header x-admin-pass)

Password: set ADMIN_PASSWORD below, or the environment variable ADMIN_PASSWORD.
"""
import json, os, sys, time, shutil, secrets, email, gzip, mimetypes
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "yasmeen2026")   # <-- change me
PORT = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8080))

ROOT = os.path.dirname(os.path.abspath(__file__))
MENU_FILE = os.path.join(ROOT, "menu.json")
BACKUP_DIR = os.path.join(ROOT, "backups")
UPLOAD_DIR = os.path.join(ROOT, "uploads")
MAX_UPLOAD = 6 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
COMPRESS = {".html", ".svg", ".css", ".js", ".json", ".txt"}
LONG_CACHE = (".webp", ".jpg", ".png", ".gif", ".svg", ".woff2", ".ttf")   # images/fonts: cache for a week
PRIVATE = {"/menu.json", "/server.py", "/start-server.bat"}   # not served as static files


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    # ---------- helpers ----------
    def wants_gzip(self):
        return "gzip" in (self.headers.get("Accept-Encoding") or "")

    def send_body(self, body, ctype, status=200, cache=None):
        if len(body) > 1024 and self.wants_gzip():
            body = gzip.compress(body, 6)
            encoded = True
        else:
            encoded = False
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        if cache:
            self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_body(body, "application/json; charset=utf-8", status, "no-store")

    def send_static_gzip(self, p):
        """Text files (html/svg/...) go out gzipped; returns False to fall back to the default handler."""
        if p.endswith("/"):
            p += "index.html"
        ext = os.path.splitext(p)[1].lower()
        if ext not in COMPRESS or not self.wants_gzip():
            return False
        full = os.path.realpath(os.path.join(ROOT, p.lstrip("/")))
        if not full.startswith(ROOT + os.sep) or not os.path.isfile(full):
            return False
        with open(full, "rb") as f:
            body = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ext == ".svg":
            ctype += "; charset=utf-8"
        cache = "public, max-age=604800" if ext in LONG_CACHE else "no-cache"
        self.send_body(body, ctype, 200, cache)
        return True

    def read_body(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("too large")
        return self.rfile.read(n)

    def authed(self):
        return secrets.compare_digest(self.headers.get("x-admin-pass", ""), ADMIN_PASSWORD)

    def path_only(self):
        return self.path.split("?", 1)[0]

    # ---------- routes ----------
    def do_GET(self):
        p = self.path_only()
        if p == "/api/menu":
            try:
                with open(MENU_FILE, encoding="utf-8") as f:
                    return self.send_json(json.load(f))
            except FileNotFoundError:
                return self.send_json({"error": "menu.json not found"}, 404)
        if p in PRIVATE or p.startswith("/backups/"):
            return self.send_error(404)
        if self.send_static_gzip(p):
            return
        return super().do_GET()

    def do_HEAD(self):
        if self.path_only() in PRIVATE:
            return self.send_error(404)
        return super().do_HEAD()

    def do_POST(self):
        p = self.path_only()
        try:
            if p == "/api/login":
                data = json.loads(self.read_body(10_000) or b"{}")
                ok = secrets.compare_digest(str(data.get("password", "")), ADMIN_PASSWORD)
                return self.send_json({"ok": ok}, 200 if ok else 401)

            if p == "/api/menu":
                if not self.authed():
                    return self.send_json({"error": "unauthorized"}, 401)
                data = json.loads(self.read_body(5 * 1024 * 1024))
                menu = data.get("menu")
                if not isinstance(menu, dict) or not isinstance(menu.get("blocks"), list):
                    return self.send_json({"error": "bad menu format"}, 400)
                os.makedirs(BACKUP_DIR, exist_ok=True)
                if os.path.exists(MENU_FILE):
                    shutil.copy2(MENU_FILE, os.path.join(BACKUP_DIR, time.strftime("menu-%Y%m%d-%H%M%S.json")))
                tmp = MENU_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(menu, f, ensure_ascii=False, indent=2)
                os.replace(tmp, MENU_FILE)
                return self.send_json({"ok": True})

            if p == "/api/upload":
                if not self.authed():
                    return self.send_json({"error": "unauthorized"}, 401)
                raw = self.read_body(MAX_UPLOAD + 64 * 1024)
                msg = email.message_from_bytes(
                    b"Content-Type: " + self.headers.get("Content-Type", "").encode() + b"\r\n\r\n" + raw)
                for part in (msg.get_payload() if msg.is_multipart() else []):
                    if part.get_param("name", header="content-disposition") != "image":
                        continue
                    ext = IMAGE_TYPES.get(part.get_content_type())
                    if not ext:
                        return self.send_json({"error": "نوع الصورة غير مدعوم"}, 400)
                    blob = part.get_payload(decode=True) or b""
                    if len(blob) > MAX_UPLOAD:
                        return self.send_json({"error": "الصورة أكبر من 6 ميجا"}, 400)
                    os.makedirs(UPLOAD_DIR, exist_ok=True)
                    name = time.strftime("offer-%Y%m%d-%H%M%S-") + secrets.token_hex(3) + ext
                    with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
                        f.write(blob)
                    return self.send_json({"ok": True, "url": "uploads/" + name})
                return self.send_json({"error": "no image"}, 400)

            return self.send_json({"error": "not found"}, 404)
        except ValueError:
            return self.send_json({"error": "bad request"}, 400)

    def end_headers(self):
        # only for files served by SimpleHTTPRequestHandler (send_body sets its own)
        if not self._headers_buffer or b"Cache-Control" not in b"".join(self._headers_buffer):
            p = self.path_only()
            if p.endswith((".html", "/")):
                self.send_header("Cache-Control", "no-cache")
            elif p.lower().endswith(LONG_CACHE):
                self.send_header("Cache-Control", "public, max-age=604800")
        super().end_headers()


if __name__ == "__main__":
    if os.name == "nt":
        # On Windows, address reuse would let a 2nd server silently share the port
        # with an old one (which then answers the admin requests). Refuse instead.
        ThreadingHTTPServer.allow_reuse_address = False
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError:
        print(f"\n  Port {PORT} is already in use - close the other server window first, then run again.\n")
        sys.exit(1)
    print(f"  Menu + admin server running on port {PORT}  (admin: /admin.html)")
    srv.serve_forever()
