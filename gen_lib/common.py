"""
gen_lib/common.py — shared utilities for all generators.

Every platform module imports save_image() from here.
No generator writes its own image saving logic — metadata is guaranteed.
"""

import os
from pathlib import Path
from datetime import datetime
from io import BytesIO

# ── Paths ───────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path.home() / "workspace" / "output" / "images"


# ── Env ─────────────────────────────────────────────────────────────────────

def load_env():
    """Load env vars from ~/.hermes/.env"""
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def get_key(name: str) -> str:
    """Get API key or die with message."""
    val = os.environ.get(name, "")
    if not val:
        print(f"❌ Missing {name}. Set it in ~/.hermes/.env")
        import sys
        sys.exit(1)
    return val


# ── Timestamp ───────────────────────────────────────────────────────────────

def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


# ── Image Saving (the metadata guarantee) ───────────────────────────────────

def save_image(data: bytes, *, prefix: str = "gen", prompt: str = "",
               model: str = "", seed: int = None, lora_id: str = None,
               steps: int = 28, negative_prompt: str = "") -> Path:
    """Save image data as PNG with AUTOMATIC1111-compatible metadata embedded.

    EVERY call to this function produces a PNG with full parameters in the
    ``parameters`` tEXt chunk.  No exceptions.  New platforms must call this
    function — they should never write raw bytes to OUTPUT_DIR.

    Returns the output Path.
    """
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    img = Image.open(BytesIO(data))
    fname = f"{prefix}_{timestamp()}.png"
    out = OUTPUT_DIR / fname

    # Build metadata in AUTOMATIC1111 format
    meta_parts = [f"Prompt: {prompt}"]
    if negative_prompt:
        meta_parts.append(f"Negative prompt: {negative_prompt}")
    params_line = [f"Steps: {steps}", f"Seed: {seed or 0}",
                   f"Size: {img.size[0]}x{img.size[1]}"]
    if model:
        params_line.append(f"Model: {model}")
    if lora_id:
        params_line.append(f"Lora: {lora_id}")

    meta_string = ", ".join(meta_parts + params_line)

    png_info = PngInfo()
    png_info.add_text("parameters", meta_string)
    img.save(out, "PNG", pnginfo=png_info)

    print(f"✅ Saved: {out}  ({out.stat().st_size:,} bytes)")
    return out


# ── Helpers ─────────────────────────────────────────────────────────────────

def image_to_data_url(path: str | Path) -> str:
    """Convert a local image file to a base64 data URL."""
    import base64
    img_data = Path(path).read_bytes()
    ext = Path(path).suffix.lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp"}
    mime = mime_map.get(ext, "image/png")
    return f"data:{mime};base64,{base64.b64encode(img_data).decode()}"


def http_post(url: str, payload: dict | list, api_key: str,
              auth_header: str = "Authorization",
              auth_prefix: str = "Bearer",
              extra_headers: dict = None,
              timeout: int = 120) -> dict:
    """POST JSON payload, return parsed JSON response. Exits on error."""
    import json
    import urllib.request
    import urllib.error
    import sys
    headers = {
        "Content-Type": "application/json",
        auth_header: f"{auth_prefix} {api_key}",
    }
    if extra_headers:
        headers.update(extra_headers)

    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"❌ HTTP {e.code}: {body[:500]}")
        sys.exit(1)


def download_bytes(url: str, timeout: int = 60) -> bytes:
    """Download a URL and return its raw bytes."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "gen.py/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()
