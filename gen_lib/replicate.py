"""
gen_lib/replicate.py — Replicate image & video generation.

Platforms:
  - FLUX NSFW (image, ~$0.025/张, 20s)
  - Wan 2.1 Uncensored (video, $0.39/条, ~5min)

Both use async prediction API: POST → poll → download.
"""

import json
import time
import base64
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from gen_lib.common import get_key, save_image, OUTPUT_DIR

FLUX_NSFW_VERSION = "fb4f086702d6a301ca32c170d926239324a7b7b2f0afc3d232a9c4be382dc3fa"
SDXL_LORA_VERSION = "89eb212b3d1366a83e949c12a4b45dfe6b6b313b594cb8268e864931ac9ffb16"
WAN_NSFW_VERSION = "46cfc445b5f89469deb11b5d8227ff9e3bb129c8920f3886cd78c426f43204c4"

REPLICATE_MODELS = {
    "flux": {"version": FLUX_NSFW_VERSION, "name": "FLUX.1-dev NSFW"},
    "sdxl": {"version": SDXL_LORA_VERSION, "name": "SDXL Multi-LoRA"},
}


def _poll_replicate(pred_id: str, api_key: str, timeout_sec: int) -> str:
    """Poll Replicate prediction until success. Returns output URL."""
    for i in range(timeout_sec // 5):
        time.sleep(5)
        poll_req = urllib.request.Request(
            f"https://api.replicate.com/v1/predictions/{pred_id}",
            headers={"Authorization": f"Bearer {api_key}",
                     "User-Agent": "gen.py/2.0"},
        )
        try:
            with urllib.request.urlopen(poll_req, timeout=15) as resp:
                result = json.loads(resp.read())
        except Exception:
            continue

        status = result.get("status", "")
        if status == "succeeded":
            output = result.get("output", "")
            if isinstance(output, str) and output.startswith("http"):
                return output
            elif isinstance(output, list) and output:
                return output[0]
            print(f"❌ No output in prediction")
            sys.exit(1)
        elif status == "failed":
            print(f"❌ Failed: {result.get('error', '?')}")
            sys.exit(1)

    print(f"❌ Timeout waiting for Replicate")
    sys.exit(1)


# ── Image ───────────────────────────────────────────────────────────────

def generate(prompt: str, *, model_key: str = "flux",
             negative_prompt: str = "",
             lora_url: str = None, lora_scale: float = 1.0,
             image_path: str = None, strength: float = 0.7,
             steps: int = 28) -> Path:
    """Generate NSFW image via Replicate.

    model_key: "flux" (FLUX.1-dev NSFW) or "sdxl" (SDXL Multi-LoRA)
    """
    api_key = get_key("REPLICATE_API_TOKEN")
    model_info = REPLICATE_MODELS.get(model_key, REPLICATE_MODELS["flux"])
    version = model_info["version"]

    print(f"🎨 Replicate: {model_info['name']}")
    print(f"📝 Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    payload = {
        "version": version,
        "input": {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": 1024,
            "height": 768,
            "num_inference_steps": steps,
            "guidance_scale": 3.5,
        },
    }

    # SDXL model supports disable_safety_checker
    if model_key == "sdxl":
        payload["input"]["disable_safety_checker"] = True

    if image_path:
        img_data = Path(image_path).read_bytes()
        mime = "image/png" if str(image_path).endswith(".png") else "image/jpeg"
        b64 = base64.b64encode(img_data).decode()
        payload["input"]["image"] = f"data:{mime};base64,{b64}"
        payload["input"]["strength"] = strength
        print(f"🖼️  Reference: {Path(image_path).name} (strength={strength})")

    if lora_url:
        payload["input"]["lora_weights"] = lora_url
        payload["input"]["lora_scale"] = lora_scale
        print(f"   LoRA: {lora_url.split('/')[-1].split('?')[0]} (scale={lora_scale})")
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.replicate.com/v1/predictions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "gen.py/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"❌ Replicate HTTP {e.code}: {body[:500]}")
        sys.exit(1)

    pred_id = result.get("id", "")
    if not pred_id:
        print(f"❌ No prediction ID")
        sys.exit(1)

    img_url = _poll_replicate(pred_id, api_key, timeout_sec=300)
    img_data = urllib.request.urlopen(
        urllib.request.Request(img_url, headers={"User-Agent": "gen.py/2.0"})
    ).read()

    return save_image(img_data, prefix="replicate", prompt=prompt,
                      model="Replicate FLUX.1-dev NSFW", lora_id=lora_url)


# ── Video ────────────────────────────────────────────────────────────────

def generate_video(prompt: str, *, image_url: str = None,
                   aspect_ratio: str = "16:9", frames: int = 81,
                   resolution: str = "480p", negative_prompt: str = "",
                   lora_strength: float = 1.0, seed: int = None) -> Path:
    """Generate NSFW video via Replicate Wan2.1 Uncensored."""
    api_key = get_key("REPLICATE_API_TOKEN")

    if not prompt.lower().startswith("unai"):
        prompt = f"unai, {prompt}"

    print(f"🎬 Wan2.1 Uncensored Video")
    print(f"📝 Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    print(f"   Aspect: {aspect_ratio}  Frames: {frames}  Res: {resolution}")

    payload = {
        "version": WAN_NSFW_VERSION,
        "input": {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "aspect_ratio": aspect_ratio,
            "frames": frames,
            "resolution": resolution,
            "lora_strength_model": lora_strength,
            "lora_strength_clip": lora_strength,
            "fast_mode": "Balanced",
            "sample_steps": 30,
            "sample_guide_scale": 5,
        },
    }
    if image_url:
        payload["input"]["image"] = image_url
    if seed is not None:
        payload["input"]["seed"] = seed

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.replicate.com/v1/predictions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "gen.py/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"❌ Replicate HTTP {e.code}: {body[:500]}")
        sys.exit(1)

    pred_id = result.get("id", "")
    if not pred_id:
        print(f"❌ No prediction ID")
        sys.exit(1)

    print(f"⏳ Waiting for video... (prediction {pred_id})")
    t0 = time.time()
    vid_url = _poll_replicate(pred_id, api_key, timeout_sec=600)
    elapsed = time.time() - t0

    vid_data = urllib.request.urlopen(
        urllib.request.Request(vid_url, headers={"User-Agent": "gen.py/2.0"}),
        timeout=120,
    ).read()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"wan_nsfw_{ts}.mp4"
    out_path.write_bytes(vid_data)
    print(f"✅ Saved: {out_path}  ({len(vid_data):,} bytes)")
    print(f"⏱️  {elapsed:.1f}s total")
    return out_path
