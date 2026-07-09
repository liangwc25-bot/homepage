"""
gen_lib/grok.py — xAI Grok Imagine image generation.

- Text-to-image: grok-imagine-image (~$0.02/张)
- Image-to-image: grok-imagine-image-quality (~$0.02/张)
- 审核宽松，但 JK 校服+性感会拦截
"""

from pathlib import Path
from gen_lib.common import get_key, save_image, http_post, download_bytes
import base64
import sys


def generate(prompt: str, *, negative_prompt: str = "",
             image_path: str = None) -> Path:
    """Generate / edit image via xAI Grok Imagine."""
    api_key = get_key("XAI_API_KEY")

    model = "grok-imagine-image-quality" if image_path else "grok-imagine-image"
    print(f"🎨 xAI Grok Imagine ($0.02/张)")
    print(f"📝 Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    payload = {
        "model": model,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "n": 1,
        "response_format": "url",
    }

    if image_path:
        img_data = Path(image_path).read_bytes()
        mime = "image/png" if image_path.endswith(".png") else "image/jpeg"
        b64 = base64.b64encode(img_data).decode()
        payload["image"] = f"data:{mime};base64,{b64}"
        print(f"🖼️  Reference: {image_path}")

    result = http_post("https://api.x.ai/v1/images/generations", payload, api_key)

    images = result.get("data", [])
    if not images:
        print(f"❌ No images in response")
        sys.exit(1)

    img_url = images[0].get("url", "")
    if not img_url:
        print(f"❌ No image URL")
        sys.exit(1)

    img_data = download_bytes(img_url)
    prefix = "grok_i2i" if image_path else "grok"
    return save_image(img_data, prefix=prefix, prompt=prompt, model=model)
