#!/usr/bin/env python3
"""Gallery server — serves image gallery with PNG metadata display."""
import json
import os
import io
import subprocess
import math
import shutil
import time
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
from pathlib import Path
from PIL import Image
from PIL.PngImagePlugin import PngInfo

IMAGES_DIR = Path.home() / "workspace" / "output" / "images"
ARCHIVE_DIR = Path.home() / "workspace" / "output" / "archived"
TRASH_DIR = Path.home() / "workspace" / "output" / ".trash"
FAVORITES_FILE = Path.home() / ".hermes" / "gallery_favorites.json"
PORT = 8089
THUMB_SIZE = (300, 300)
CACHE = {}  # {"main": [...], "archive": [...]}

def load_favorites() -> list:
    """Load favorited filenames."""
    if FAVORITES_FILE.exists():
        try:
            data = json.loads(FAVORITES_FILE.read_text())
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []

def save_favorites(files: list):
    """Save favorited filenames."""
    FAVORITES_FILE.parent.mkdir(parents=True, exist_ok=True)
    FAVORITES_FILE.write_text(json.dumps(files, indent=2))

def toggle_favorite(filename: str) -> bool:
    """Toggle a filename in favorites. Returns new state (True=favorited)."""
    favs = load_favorites()
    if filename in favs:
        favs.remove(filename)
        save_favorites(favs)
        CACHE.pop("main", None)
        CACHE.pop("archive", None)
        return False
    else:
        favs.append(filename)
        save_favorites(favs)
        CACHE.pop("main", None)
        CACHE.pop("archive", None)
        return True

def is_archived(filename: str) -> bool:
    """Check if a file exists in the archive directory."""
    return (ARCHIVE_DIR / filename).exists()

def move_to_trash(filename: str) -> dict | None:
    """Move an image file to the trash directory. Tries main dir first, then archive.
    Returns {trashed: true, filename: str} or None if not found."""
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    for src_dir in (IMAGES_DIR, ARCHIVE_DIR):
        fp = src_dir / filename
        if fp.exists():
            shutil.move(str(fp), str(TRASH_DIR / filename))
            CACHE.pop("main", None)
            CACHE.pop("archive", None)
            return {"trashed": True, "filename": filename}
    return None

def toggle_archive(filename: str) -> dict | None:
    """Move file between main and archive dirs. Returns {archived: bool, filename: str} or None if not found."""
    main_path = IMAGES_DIR / filename
    arch_path = ARCHIVE_DIR / filename

    if main_path.exists():
        # Archive: move main → archive
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(main_path), str(arch_path))
        # Invalidate both caches
        CACHE.pop("main", None)
        CACHE.pop("archive", None)
        return {"archived": True, "filename": filename}
    elif arch_path.exists():
        # Unarchive: move archive → main
        shutil.move(str(arch_path), str(main_path))
        CACHE.pop("main", None)
        CACHE.pop("archive", None)
        return {"archived": False, "filename": filename}
    return None

def parse_png_metadata(filepath: Path) -> dict:
    """Extract tEXt parameters chunk from a PNG file."""
    meta = {"prompt": "", "seed": "", "model": "", "params": ""}
    try:
        img = Image.open(filepath)
        for key, value in img.text.items():
            if key == "parameters":
                meta["params"] = value
                # Try newline-separated format (AUTOMATIC1111: "Prompt: xxx\nSteps: 28, Seed: ...")
                lines = value.split("\n")
                for line in lines:
                    line = line.strip()
                    if line.startswith("Prompt: "):
                        prompt_raw = line[8:].strip()
                        for sep in [", Negative prompt:", "\nNegative prompt:"]:
                            idx = prompt_raw.find(sep)
                            if idx > 0:
                                prompt_raw = prompt_raw[:idx]
                                break
                        meta["prompt"] = prompt_raw
                    elif line.startswith("Steps:"):
                        parts = line.split(", ")
                        for part in parts:
                            if part.startswith("Seed: "):
                                meta["seed"] = part[6:].strip()
                            elif part.startswith("Model: "):
                                meta["model"] = part[7:].strip()
                # Fallback
                if not meta["prompt"]:
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith("Negative") and not line.startswith("Steps"):
                            meta["prompt"] = line
                            break
                if not meta.get("seed") or not meta.get("model"):
                    parts = value.split(", ")
                    for part in parts:
                        if part.startswith("Seed: "):
                            meta["seed"] = part[6:].strip()
                        elif part.startswith("Model: "):
                            meta["model"] = part[7:].strip()
                break
        img.close()
    except Exception:
        pass
    return meta

def get_images(source="main", force_refresh=False) -> list:
    """Fast scan — filenames + stat only, no PNG metadata parsing.
    source: 'main' (IMAGES_DIR) or 'archive' (ARCHIVE_DIR). Cached per source."""
    cache_key = source
    if CACHE.get(cache_key) is not None and not force_refresh:
        return CACHE[cache_key]

    target_dir = ARCHIVE_DIR if source == "archive" else IMAGES_DIR
    images = []
    if not target_dir.exists():
        CACHE[cache_key] = images
        return images

    favs = load_favorites()

    for f in sorted(target_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4"):
            st = f.stat()
            images.append({
                "filename": f.name,
                "size_kb": st.st_size // 1024,
                "mtime": int(st.st_mtime),
                "prompt": "",
                "seed": "",
                "model": "",
                "params": "",
                "favorited": f.name in favs,
                "archived": source == "archive",
            })

    CACHE[cache_key] = images
    return images

def make_thumbnail(filepath: Path) -> bytes | None:
    """Generate thumbnail, return PNG bytes. Returns None on failure."""
    try:
        img = Image.open(filepath)
        img.thumbnail(THUMB_SIZE, Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        img.close()
        return buf.getvalue()
    except Exception:
        return None

def unq(s):
    return urllib.parse.unquote(s)

class GalleryHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        try:
            self._do_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()

    def _do_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        # Serve gallery HTML
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(Path(__file__).parent / "gallery.html") as f:
                self.wfile.write(f.read().encode())
            return

        # PWA static files
        _static_files = {
            "/manifest.json": ("application/json", "gallery-manifest.json"),
            "/sw.js": ("application/javascript", "gallery-sw.js"),
            "/icon-192.png": ("image/png", "gallery-icon-192.png"),
            "/icon-512.png": ("image/png", "gallery-icon-512.png"),
        }
        if path in _static_files:
            mime, fname = _static_files[path]
            fpath = Path(__file__).parent / fname
            if fpath.exists():
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(fpath.read_bytes())
            else:
                self.send_error(404)
            return

        # API: list images
        if path == "/api/images":
            page = int(params.get("page", [1])[0])
            per_page = int(params.get("per_page", [50])[0])
            filter_mode = params.get("filter", ["all"])[0]

            source = "archive" if filter_mode == "archive" else "main"
            all_images = get_images(source)

            # For fav filter, scan BOTH dirs (favs may be in archive)
            if filter_mode == "fav":
                all_images = get_images("main") + get_images("archive")
                all_images = [img for img in all_images if img["favorited"]]
                # Re-sort by mtime desc
                all_images.sort(key=lambda x: x["mtime"], reverse=True)

            total = len(all_images)
            total_pages = max(1, math.ceil(total / per_page))
            start = (page - 1) * per_page
            end = start + per_page
            page_images = all_images[start:end]

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "images": page_images,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            }).encode())
            return

        # API: rescan
        if path == "/api/rescan":
            CACHE.clear()
            main_count = len(get_images("main"))
            arch_count = len(get_images("archive"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "count": main_count,
                "archived_count": arch_count,
            }).encode())
            return

        # API: toggle favorite
        if path == "/api/favorite":
            filename = params.get("filename", [None])[0]
            if filename:
                new_state = toggle_favorite(filename)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"favorited": new_state, "filename": filename}).encode())
            else:
                self.send_error(400, "Missing filename")
            return

        # API: list favorites
        if path == "/api/favorites":
            favs = load_favorites()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"favorites": favs}).encode())
            return

        # API: toggle archive
        if path == "/api/archive":
            filename = params.get("filename", [None])[0]
            if filename:
                result = toggle_archive(filename)
                if result is None:
                    self.send_error(404, "File not found in main or archive")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            else:
                self.send_error(400, "Missing filename")
            return

        # API: list archived filenames
        if path == "/api/archived":
            archived = []
            if ARCHIVE_DIR.exists():
                for f in ARCHIVE_DIR.iterdir():
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                        archived.append(f.name)
            archived.sort()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"archived": archived}).encode())
            return

        # API: move to trash
        if path == "/api/trash":
            filename = params.get("filename", [None])[0]
            if filename:
                result = move_to_trash(filename)
                if result is None:
                    self.send_error(404, "File not found")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            else:
                self.send_error(400, "Missing filename")
            return

        # API: single-file metadata (on-demand, lazy)
        if path == "/api/meta":
            filename = params.get("filename", [None])[0]
            if filename:
                filepath = IMAGES_DIR / filename
                if not filepath.exists():
                    filepath = ARCHIVE_DIR / filename
                if filepath.exists():
                    meta = parse_png_metadata(filepath)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "filename": filename,
                        "prompt": meta["prompt"],
                        "seed": meta["seed"],
                        "model": meta["model"],
                        "params": meta["params"],
                    }).encode())
                else:
                    self.send_error(404, "File not found")
            else:
                self.send_error(400, "Missing filename")
            return

        # API: send to Telegram
        if path == "/api/send":
            filename = params.get("filename", [None])[0]
            if filename:
                filepath = IMAGES_DIR / filename
                if not filepath.exists():
                    filepath = ARCHIVE_DIR / filename
                if filepath.exists():
                    try:
                        result = subprocess.run(
                            ["/root/scripts/tg", str(filepath)],
                            capture_output=True, text=True, timeout=120
                        )
                        ok = result.returncode == 0
                        self.send_response(200 if ok else 500)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "sent": ok,
                            "filename": filename,
                            "error": result.stderr.strip() if not ok else None,
                        }).encode())
                    except subprocess.TimeoutExpired:
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "sent": False, "filename": filename,
                            "error": "timeout",
                        }).encode())
                else:
                    self.send_error(404, "File not found")
            else:
                self.send_error(400, "Missing filename")
            return

        # Serve thumbnail — try main dir first, then archive
        if path.startswith("/thumb/"):
            filename = unq(path[7:])
            filepath = IMAGES_DIR / filename
            if not filepath.exists():
                filepath = ARCHIVE_DIR / filename
            if filepath.exists():
                thumb = make_thumbnail(filepath)
                if thumb is None:
                    self.send_error(415, "Unsupported image")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(thumb)
            else:
                self.send_error(404)
            return

        # Serve raw image — try main dir first, then archive
        if path.startswith("/raw/"):
            filename = unq(path[5:])
            filepath = IMAGES_DIR / filename
            if not filepath.exists():
                filepath = ARCHIVE_DIR / filename
            if filepath.exists():
                ext = filepath.suffix.lower()
                mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
                ct = mime.get(ext.lstrip("."), "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                try:
                    with open(filepath, "rb") as f:
                        self.wfile.write(f.read())
                except Exception:
                    pass
            else:
                self.send_error(404)
            return

        self.send_error(404)

    def log_message(self, format, *args):
        pass  # quiet

if __name__ == "__main__":
    print(f"🚀 Gallery server on http://127.0.0.1:{PORT}")
    print(f"   Images: {IMAGES_DIR}")
    print(f"   Archive: {ARCHIVE_DIR}")
    import threading
    total_files = len(list(IMAGES_DIR.glob("*"))) if IMAGES_DIR.exists() else 0
    print(f"   Pre-warming cache ({total_files}+ files) in background...")
    threading.Thread(target=lambda: (get_images("main"), get_images("archive")), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), GalleryHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
