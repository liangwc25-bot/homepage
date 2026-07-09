"""
gen_lib/together.py — Together AI image generation.

Models:
  - dreamshaper (cheapest SFW, $0.0006/1M tokens)
  - SD XL ($0.0019)
  - FLUX variants via serverless API
  - LoRA: only via dedicated FLUX.1-dev-lora model
"""

from gen_lib.common import get_key, save_image, http_post
from pathlib import Path
import base64
import sys

MODELS = {
    "dreamshaper": {"id": "dreamshaper", "name": "Dreamshaper", "price": "$0.0006/1M tokens"},
    "sdxl": {"id": "stabilityai/stable-diffusion-xl-base-1.0", "name": "SD XL", "price": "$0.0019/1M tokens"},
    "sd3": {"id": "stabilityai/stable-diffusion-3-medium", "name": "Stable Diffusion 3", "price": "$0.0019/1M tokens"},
    "flux-schnell": {"id": "black-forest-labs/FLUX.1-schnell", "name": "FLUX.1 Schnell", "price": "$0.0027/1M tokens"},
    "flux-dev": {"id": "black-forest-labs/FLUX.2-dev", "name": "FLUX.2 Dev", "price": "$0.0154/image"},
    "flux-pro": {"id": "black-forest-labs/FLUX.2-pro", "name": "FLUX.2 Pro", "price": "$0.03/image"},
    "flux-max": {"id": "black-forest-labs/FLUX.2-max", "name": "FLUX.2 Max", "price": "$0.07/1M tokens"},
    "flux-flex": {"id": "black-forest-labs/FLUX.2-flex", "name": "FLUX.2 Flex", "price": "$0.03/image"},
    "juggernaut": {"id": "Rundiffusion/Juggernaut-Lightning-Flux", "name": "Juggernaut Lightning Flux", "price": "$0.0017/1M tokens"},
    "juggernaut-pro": {"id": "RunDiffusion/Juggernaut-pro-flux", "name": "Juggernaut Pro Flux", "price": "$0.0049/1M tokens"},
    "seedream3": {"id": "ByteDance-Seed/Seedream-3.0", "name": "Seedream 3.0", "price": "$0.018/1M tokens"},
    "seedream4": {"id": "ByteDance-Seed/Seedream-4.0", "name": "Seedream 4.0", "price": "$0.03/image"},
    "imagen-fast": {"id": "google/imagen-4.0-fast", "name": "Imagen 4.0 Fast", "price": "$0.02/1M tokens"},
    "imagen-ultra": {"id": "google/imagen-4.0-ultra", "name": "Imagen 4.0 Ultra", "price": "$0.06/1M tokens"},
}


def generate(prompt: str, *, model_key: str = "dreamshaper",
             negative_prompt: str = "", lora_url: str = None,
             lora_scale: float = 0.8) -> Path:
    """Generate image via Together AI."""
    api_key = get_key("TOGETHER_API_KEY")

    if lora_url:
        model_id = "black-forest-labs/FLUX.1-dev-lora"
        model_name = "FLUX.1-dev + LoRA"
        price = "$0.0154/image + LoRA"
    elif model_key not in MODELS:
        print(f"❌ Unknown model: {model_key}")
        print(f"   Available: {', '.join(MODELS.keys())}")
        sys.exit(1)
    else:
        model_info = MODELS[model_key]
        model_id = model_info["id"]
        model_name = model_info["name"]
        price = model_info["price"]

    print(f"🎨 Together AI: {model_name} ({price})")
    print(f"📝 Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    payload = {
        "model": model_id,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "width": 1024,
        "height": 768,
        "steps": 28 if lora_url else 4,
        "n": 1,
        "response_format": "b64_json",
    }

    if lora_url:
        payload["image_loras"] = [{"path": lora_url, "scale": lora_scale}]
        print(f"🔗 LoRA: {lora_url[:60]}... (scale={lora_scale})")

    result = http_post("https://api.together.xyz/v1/images/generations",
                       payload, api_key)

    images = result.get("data", [])
    if not images:
        print(f"❌ No images in response")
        sys.exit(1)

    # Handle b64_json response
    b64_data = images[0].get("b64_json", "")
    if b64_data:
        img_data = base64.b64decode(b64_data)
        return save_image(img_data, prefix=f"together_{model_key}",
                          prompt=prompt, model=model_name)

    # Handle URL response
    img_url = images[0].get("url", "")
    if img_url:
        from urllib.request import Request, urlopen
        req = Request(img_url)
        with urlopen(req, timeout=30) as resp:
            img_data = resp.read()
        return save_image(img_data, prefix=f"together_{model_key}",
                          prompt=prompt, model=model_name)

    print(f"❌ No image data in response")
    sys.exit(1)
