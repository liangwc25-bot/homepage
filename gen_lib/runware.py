"""
gen_lib/runware.py — Runware AI image generation.

Premier platform: cheapest FLUX ($0.0013), Pony SDXL, CivitAI LoRA,
IP-Adapter face preservation, NSFW via safety.checkContent=false.

Image-to-image requires two-step flow: imageUpload → seedImage UUID.
"""

import uuid
import json
import base64
import sys
import urllib.request
import urllib.error
from pathlib import Path
from gen_lib.common import get_key, save_image, http_post, download_bytes

MODELS = {
    "flux-dev":       {"id": "runware:101@1", "name": "FLUX.1-dev", "price": "$0.0013/张"},
    "flux-schnell":   {"id": "bfl:1@1", "name": "FLUX Schnell", "price": "$0.0023/张"},
    "flux-2-pro":     {"id": "bfl:5@1", "name": "FLUX.2 Pro", "price": "$0.045/张"},
    "pony":           {"id": "runware:777@1", "name": "Pony V7 (AuraFlow)", "price": "~$0.005/张"},
    "sdxl":           {"id": "runware:100@1", "name": "SDXL", "price": "~$0.003/张"},
    "pony-xl":        {"id": "liangwc:3@1", "name": "Prefect Pony XL v3", "price": "$0.0013/张"},
    "prefect-ill-xl": {"id": "liangwc:6@1", "name": "Prefect Illustrious XL v8", "price": "~$0.003/张"},
    "qwen-edit":      {"id": "runware:108@20", "name": "Qwen-Image-Edit", "price": "$0.0019/次"},
}

# Aspect ratio → (width, height)
ASPECT_MAP = {
    "16:9": (1024, 768),
    "9:16": (768, 1024),
    "1:1":  (1024, 1024),
    "3:2":  (1152, 768),
    "2:3":  (768, 1152),
    "4:3":  (1088, 832),
    "3:4":  (832, 1088),
}

API_URL = "https://api.runware.ai/v1"


def _upload_image(api_key: str, data_uri: str) -> str:
    """Upload image to Runware, return imageUUID for use with seedImage."""
    payload = [{
        "taskType": "imageUpload",
        "taskUUID": str(uuid.uuid4()),
        "image": data_uri,
    }]
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_URL, data=data,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"❌ Runware imageUpload failed: {body[:300]}")
        sys.exit(1)

    upload_data = result.get("data", [])
    if not upload_data or not upload_data[0].get("imageUUID"):
        print(f"❌ Runware upload no imageUUID")
        sys.exit(1)
    return upload_data[0]["imageUUID"]


def generate(prompt: str, *, model_key: str = "flux-dev",
             negative_prompt: str = "", image_path: str = None,
             strength: float = 0.8, lora_id: str = None,
             lora_scale: float = 0.8, seed: int = None,
             aspect: str = "9:16", cfg_scale: float = None) -> Path:
    """Generate image via Runware AI."""
    api_key = get_key("RUNWARE_API_KEY")

    if model_key not in MODELS:
        print(f"❌ Unknown model: {model_key}")
        print(f"   Available: {', '.join(MODELS.keys())}")
        sys.exit(1)

    model_info = MODELS[model_key]
    model_id = model_info["id"]
    is_qwen = (model_key == "qwen-edit")

    print(f"🎨 Runware: {model_info['name']} ({model_info['price']})")
    print(f"📝 Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    w, h = ASPECT_MAP.get(aspect, (1024, 768))

    task = {
        "taskType": "imageInference",
        "taskUUID": str(uuid.uuid4()),
        "model": model_id,
        "positivePrompt": prompt,
        "negativePrompt": negative_prompt or "ugly, deformed, bad anatomy",
        "width": w,
        "height": h,
        "steps": 28,
        "CFGScale": cfg_scale if cfg_scale is not None else (6.0 if model_key in ("pony-xl", "prefect-ill-xl") else 3.5),
        "safety": {"checkContent": False},
        "outputFormat": "PNG",
        "includeCost": True,
        "numberResults": 1,
    }
    if seed is not None:
        task["seed"] = seed

    # Image-to-image: two-step flow for Runware (non-Qwen)
    if image_path:
        img_data = Path(image_path).read_bytes()
        mime = "image/png" if str(image_path).endswith(".png") else "image/jpeg"
        b64 = base64.b64encode(img_data).decode()
        data_uri = f"data:{mime};base64,{b64}"

        if is_qwen:
            # Qwen uses referenceImages (direct data URI), not seedImage
            task["referenceImages"] = [data_uri]
        else:
            image_uuid = _upload_image(api_key, data_uri)
            task["seedImage"] = image_uuid
            task["strength"] = strength
            print(f"🖼️  Reference: {Path(image_path).name} (UUID={image_uuid[:12]}..., strength={strength})")

    # LoRA — supports single or comma-separated multiple IDs
    # Formats:
    #   lora_id="civitai:667086@746602"  lora_scale=1.0
    #   lora_id="civitai:667086@746602,civitai:888235@501154"  lora_scale="1.0,0.6"
    #   lora_id="civitai:667086@746602,civitai:888235@501154"  lora_scale=0.8  (same scale for all)
    if lora_id:
        ids = [x.strip() for x in lora_id.split(",") if x.strip()]
        if isinstance(lora_scale, str):
            scales = [float(x.strip()) for x in lora_scale.split(",") if x.strip()]
            if len(scales) == 1 and len(ids) > 1:
                scales = scales * len(ids)
            elif len(scales) < len(ids):
                scales += [0.8] * (len(ids) - len(scales))
        else:
            scales = [lora_scale] * len(ids)

        task["lora"] = [{"model": mid, "weight": s}
                        for mid, s in zip(ids, scales[:len(ids)])]
        for mid, s in zip(ids, scales[:len(ids)]):
            print(f"🔗 LoRA: {mid} (scale={s})")

    result = http_post(API_URL, [task], api_key, auth_prefix="Bearer")

    data_list = result.get("data", [])
    if not data_list:
        errors = result.get("errors", [])
        if errors:
            print(f"❌ Runware error: {errors[0].get('message', errors)}")
        else:
            print(f"❌ No data in response")
        sys.exit(1)

    img_url = data_list[0].get("imageURL", "")
    if not img_url:
        print(f"❌ No imageURL in response")
        sys.exit(1)

    cost = data_list[0].get("cost", "?")
    used_seed = data_list[0].get("seed", seed)
    print(f"💰 Cost: ${cost}  🎲 Seed: {used_seed}")

    img_data = download_bytes(img_url)
    out = save_image(img_data, prefix=f"runware_{model_key}_{used_seed}",
                     prompt=prompt, model=model_info["name"],
                     seed=used_seed, lora_id=lora_id)
    return out, used_seed
