"""
gen_lib/openrouter.py — OpenRouter image models.

Models: Gemini Flash / Pro / FLUX Klein / Seedream / GPT-5 Image
All use the chat completions endpoint with image_config.
"""

from gen_lib.common import get_key, save_image, http_post, download_bytes
from pathlib import Path
import base64
import re
import sys

MODELS = {
    "gemini-flash": {
        "id": "google/gemini-2.5-flash-image",
        "name": "Gemini 2.5 Flash Image",
        "price": "$0.0003 input + ~$0.03/image",
    },
    "gemini-flash2": {
        "id": "google/gemini-3.1-flash-image-preview",
        "name": "Gemini 3.1 Flash Image",
        "price": "$0.0005 input + ~$0.04/image",
    },
    "gemini-pro": {
        "id": "google/gemini-3-pro-image-preview",
        "name": "Gemini 3 Pro Image",
        "price": "$0.002 input + ~$0.08/image",
    },
    "flux-klein": {
        "id": "black-forest-labs/flux.2-klein-4b",
        "name": "FLUX.2 Klein 4B",
        "price": "$0.014/image (first MP)",
    },
    "flux-pro": {
        "id": "black-forest-labs/flux.2-pro",
        "name": "FLUX.2 Pro",
        "price": "$0.03/image (first MP)",
    },
    "seedream": {
        "id": "bytedance-seed/seedream-4.5",
        "name": "Seedream 4.5",
        "price": "$0.04/image",
    },
    "gpt5-mini": {
        "id": "openai/gpt-5-image-mini",
        "name": "GPT-5 Image Mini",
        "price": "~$0.03-0.05/image",
    },
    "gpt5": {
        "id": "openai/gpt-5-image",
        "name": "GPT-5 Image",
        "price": "~$0.10/image",
    },
}


def generate(prompt: str, *, model_key: str = "gemini-flash",
             negative_prompt: str = "") -> Path:
    """Generate image via OpenRouter."""
    api_key = get_key("OPENROUTER_API_KEY")

    if model_key not in MODELS:
        print(f"❌ Unknown model: {model_key}")
        print(f"   Available: {', '.join(MODELS.keys())}")
        sys.exit(1)

    model_info = MODELS[model_key]
    model_id = model_info["id"]

    print(f"🎨 {model_info['name']} ({model_info['price']})")
    print(f"📝 Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "image_config": {"aspect_ratio": "16:9"},
    }

    result = http_post(
        "https://openrouter.ai/api/v1/chat/completions",
        payload, api_key,
        extra_headers={
            "HTTP-Referer": "https://github.com/hermes-agent",
            "X-Title": "gen.py",
        },
    )

    choices = result.get("choices", [])
    if not choices:
        print(f"❌ No choices in response")
        sys.exit(1)

    message = choices[0].get("message", {})
    content = message.get("content", "")

    # Inline base64 data URL
    if "data:image" in content:
        start = content.index("data:image")
        end = content.find(")", start)
        if end == -1:
            end = len(content)
        data_url = content[start:end].rstrip('"').rstrip("'")
        _, _, b64data = data_url.partition(",")
        img_data = base64.b64decode(b64data)
        return save_image(img_data, prefix=f"or_{model_key}")

    # Image URL in content
    if "http" in content:
        urls = re.findall(r"https?://[^\s\)\"]+\.(?:jpg|jpeg|png|webp)", content)
        if urls:
            img_data = download_bytes(urls[0])
            return save_image(img_data, prefix=f"or_{model_key}")

    # Structured content (image_url parts)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                url = part.get("image_url", {}).get("url", "")
                if url.startswith("data:image"):
                    _, _, b64data = url.partition(",")
                    img_data = base64.b64decode(b64data)
                    return save_image(img_data, prefix=f"or_{model_key}")
                elif url.startswith("http"):
                    img_data = download_bytes(url)
                    return save_image(img_data, prefix=f"or_{model_key}")

    print(f"⚠️  No image found in response")
    sys.exit(1)
