"""
gen_lib/modelslab.py — ModelsLab AI image generation.

API v6 endpoint. Async submission → poll fetch.
Models: pony, sdxl, flux, and community Illustrious models.
Pricing: ~$0.0047/image for community models.
"""

import json
import time
import sys
import urllib.request
import urllib.error
from pathlib import Path
from gen_lib.common import get_key, save_image, OUTPUT_DIR, download_bytes

API_BASE = "https://modelslab.com/api/v6/images"
TEXT2IMG = f"{API_BASE}/text2img"
FETCH = f"{API_BASE}/fetch"

MODELS = {
    "pony":         {"id": "pony", "name": "Pony Diffusion", "price": "$0.0047"},
    "sdxl":         {"id": "sdxl", "name": "SDXL", "price": "$0.0047"},
    "flux":         {"id": "flux", "name": "FLUX.1-dev", "price": "~$0.0047"},
    "illustrious":  {"id": "hassaku-xl-illustrious-beta-v0-6-1751955846", "name": "Illustrious (Hassaku XL)", "price": "$0.0047"},
}


def _poll_result(request_id: int, api_key: str, timeout_sec: int = 300) -> str:
    """Poll fetch endpoint until success. Returns image URL."""
    start = time.time()
    last_status = ""
    while time.time() - start < timeout_sec:
        time.sleep(3)
        try:
            data = json.dumps({"key": api_key}).encode()
            req = urllib.request.Request(
                f"{FETCH}/{request_id}",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            print(f"⚠️ Poll error: {e}")
            continue

        status = result.get("status", "")
        if status != last_status:
            last_status = status

        if status == "success":
            output = result.get("output", [])
            if output and output[0]:
                return output[0]
            print("❌ No output URL in success response")
            sys.exit(1)
        elif status == "failed":
            msg = result.get("message", "") or result.get("messege", "") or "Unknown error"
            print(f"❌ Generation failed: {msg}")
            sys.exit(1)
        elif status == "error":
            msg = result.get("message", "") or result.get("messege", "") or "Unknown error"
            print(f"❌ API error: {msg}")
            sys.exit(1)
        # "processing" → continue polling

    print(f"❌ Timeout waiting for ModelsLab ({timeout_sec}s)")
    sys.exit(1)


def generate(prompt: str, *, model_key: str = "pony",
             negative_prompt: str = "", seed: int = None,
             width: int = 1024, height: int = 768,
             steps: int = 30, guidance: float = 7.5,
             safety_checker: bool = False,
             lora_model: str = None, lora_strength: float = 0.8) -> Path:
    """Generate image via ModelsLab.

    model_key: "pony", "sdxl", "flux", "illustrious"
    safety_checker: False to disable (default)

    Returns path to saved PNG.
    """
    api_key = get_key("MODELSLAB_API_KEY")

    if model_key not in MODELS:
        print(f"❌ Unknown model: {model_key}")
        print(f"   Available: {', '.join(MODELS.keys())}")
        sys.exit(1)

    model_info = MODELS[model_key]
    model_id = model_info["id"]

    print(f"🎨 ModelsLab: {model_info['name']} ({model_info['price']})")
    print(f"📝 Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    payload: dict = {
        "key": api_key,
        "model_id": model_id,
        "prompt": prompt,
        "negative_prompt": negative_prompt or "blurry, low quality, distorted",
        "width": width,
        "height": height,
        "samples": 1,
        "num_inference_steps": steps,
        "guidance_scale": guidance,
        "safety_checker": "no" if not safety_checker else "yes",
    }
    if seed is not None:
        payload["seed"] = seed

    # LoRA support (pre-loaded community LoRA model_id, not URL)
    if lora_model:
        payload["lora_model"] = lora_model
        payload["lora_strength"] = lora_strength
        print(f"🔗 LoRA: {lora_model} (strength={lora_strength})")

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        TEXT2IMG,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"❌ ModelsLab HTTP {e.code}: {body[:500]}")
        sys.exit(1)

    status = result.get("status", "")

    if status == "success":
        # Immediate response
        output = result.get("output", [])
        if output and output[0]:
            img_url = output[0]
        else:
            print(f"❌ No output in immediate response")
            sys.exit(1)
    elif status in ("processing", "queued"):
        request_id = result.get("id")
        if not request_id:
            print(f"❌ No request ID for polling")
            sys.exit(1)
        print(f"⏳ Processing... (ID: {request_id})")
        img_url = _poll_result(request_id, api_key)
    elif status == "error":
        msg = result.get("message", "Unknown error")
        print(f"❌ Error: {msg}")
        sys.exit(1)
    else:
        print(f"❌ Unexpected status: {status}")
        sys.exit(1)

    elapsed = time.time() - t0
    print(f"⏱️  {elapsed:.1f}s total")

    # Download image — retry once if CDN 404s (image may still be uploading)
    last_error = None
    for attempt in range(3):
        try:
            img_data = download_bytes(img_url)
            break
        except Exception as e:
            last_error = e
            if attempt < 2:
                print(f"⚠️ Download attempt {attempt+1} failed, retrying in 3s...")
                time.sleep(3)
    else:
        print(f"❌ Download failed after 3 attempts: {last_error}")
        print(f"   URL: {img_url[:120]}")
        print(f"   LoRA may not exist on ModelsLab. Check model_id.")
        sys.exit(1)

    return save_image(img_data, prefix=f"modelslab_{model_key}_{seed}",
                      prompt=prompt, model=model_info["name"], seed=seed)
