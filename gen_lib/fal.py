"""
gen_lib/fal.py — fal.ai image generation.

Platforms:
  - FLUX Schnell (fastest, $0.003/MP)
  - FLUX Pro + img2img (~$0.05-0.10/MP)
  - FLUX + LoRA (~$0.035/MP) — SFW only, safety checker blocks NSFW
"""

from pathlib import Path
from gen_lib.common import get_key, save_image, image_to_data_url, http_post, download_bytes
import sys

LORA_URL = "https://huggingface.co/liangwc25/NSFW_FLUX-1.DEV/resolve/main/NSFW_FLUX-1.DEV.safetensors"


def generate(prompt: str, *, negative_prompt: str = "", seed: int = None,
             image_path: str = None, strength: float = 0.6,
             lora: bool = False) -> Path:
    """Generate image via fal.ai."""
    api_key = get_key("FAL_KEY")

    if lora:
        endpoint = "https://fal.run/fal-ai/flux-lora"
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "lora_url": LORA_URL,
            "lora_scale": 0.8,
            "num_images": 1,
            "image_size": "landscape_16_9",
            "enable_safety_checker": False,
            "output_format": "jpeg",
        }
        prefix = "fal_lora"
        model_name = "FLUX + LoRA"
        print(f"🎨 fal.ai {model_name} (~$0.035/MP)")
    elif image_path:
        endpoint = "https://fal.run/fal-ai/flux-pro"
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "num_images": 1,
            "enable_safety_checker": False,
            "output_format": "jpeg",
            "image_size": {"width": 1024, "height": 768},
        }
        if seed is not None:
            payload["seed"] = seed
        payload["image_url"] = image_to_data_url(image_path)
        payload["strength"] = strength
        prefix = "fal_pro"
        model_name = "FLUX Pro + img2img"
        print(f"🎨 fal.ai {model_name} (~$0.05-0.10/MP)")
        print(f"🖼️  Reference: {Path(image_path).name} (strength={strength})")
    else:
        endpoint = "https://fal.run/fal-ai/flux/schnell"
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "num_inference_steps": 4,
            "num_images": 1,
            "image_size": "landscape_16_9",
            "enable_safety_checker": True,
            "output_format": "jpeg",
        }
        if seed is not None:
            payload["seed"] = seed
        prefix = "fal"
        model_name = "FLUX Schnell"
        print(f"🎨 fal.ai {model_name} ($0.003/MP)")

    print(f"📝 Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    result = http_post(endpoint, payload, api_key,
                       auth_header="Authorization", auth_prefix="Key")

    # fal.ai returns images in different formats
    images = result.get("images", result.get("data", []))
    if not images:
        url = result.get("url", result.get("image", {}).get("url", ""))
        if url:
            images = [{"url": url}]

    if not images:
        print(f"❌ No images in response")
        sys.exit(1)

    img_url = images[0].get("url", "")
    if not img_url:
        print(f"❌ No image URL")
        sys.exit(1)

    img_data = download_bytes(img_url)
    return save_image(img_data, prefix=prefix, prompt=prompt, model=model_name,
                      seed=seed)
