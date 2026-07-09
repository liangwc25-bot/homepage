#!/usr/bin/env python3
"""
gen.py — AI 图片/视频生成 dispatcher。

贴 prompt → 选平台 → 调 API → 出图。零 agent token 消耗。

平台:
  runware    Runware FLUX.1-dev/Pony/SDXL + LoRA + img2img ($0.0013/张)
  fal        fal.ai FLUX Schnell ($0.003/张)
  fal-pro    fal.ai FLUX Pro + img2img (~$0.05-0.10)
  fal-lora   fal.ai FLUX + LoRA (SFW only)
  replicate  Replicate FLUX NSFW (~$0.025/张)
  grok       xAI Grok Imagine ($0.02/张)
  together   Together AI (Dreamshaper/SDXL/etc)
  openrouter OpenRouter (Gemini/FLUX/GPT)
  wan-nsfw   Replicate Wan2.1 Uncensored Video ($0.39/条)

用法:
  python3 gen.py "prompt"                          # 默认 runware
  python3 gen.py "prompt" --platform runware
  python3 gen.py "prompt" --platform runware --model pony-xl
  python3 gen.py "prompt" --platform replicate --lora <URL>
  python3 gen.py "prompt" --platform runware --image ./ref.jpg
  python3 gen.py "prompt" --platform grok
  python3 gen.py --list-models
  python3 gen.py --interactive

Metadata:
  所有生成的 PNG 自动嵌入完整参数（prompt/seed/model/LoRA）。
  验证: python3 ~/scripts/pngmeta.py <文件>.png

环境变量 (~/.hermes/.env):
  RUNWARE_API_KEY  /  FAL_KEY  /  XAI_API_KEY
  OPENROUTER_API_KEY  /  TOGETHER_API_KEY  /  REPLICATE_API_TOKEN
"""

import argparse
import sys
import time
from pathlib import Path

# ── Dispatch table ──────────────────────────────────────────────────────────

# Each entry: (module_path, function_name, optional: platform_key)
# The dispatch talks to gen_lib directly, not CLI flags.
PLATFORM_DISPATCH = {
    "runware":    ("gen_lib.runware",    "generate"),
    "fal":        ("gen_lib.fal",        "generate"),
    "fal-pro":    ("gen_lib.fal",        "generate"),
    "fal-lora":   ("gen_lib.fal",        "generate"),
    "grok":       ("gen_lib.grok",       "generate"),
    "together":   ("gen_lib.together",   "generate"),
    "replicate":  ("gen_lib.replicate",  "generate"),
    "openrouter": ("gen_lib.openrouter", "generate"),
    "wan-nsfw":   ("gen_lib.replicate",  "generate_video"),
    "modelslab":  ("gen_lib.modelslab",  "generate"),
}


def main():
    from gen_lib.common import load_env
    load_env()

    parser = argparse.ArgumentParser(
        description="AI 图片生成 — 贴 prompt, 出图, 零 agent token",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("prompt", nargs="?", help="Image prompt")
    parser.add_argument("-n", "--negative-prompt", default="")
    parser.add_argument("-p", "--platform", default="runware",
                        choices=list(PLATFORM_DISPATCH.keys()))
    parser.add_argument("-i", "--image", help="Reference image for img2img")
    parser.add_argument("--strength", type=float, default=0.6)
    parser.add_argument("--lora", help="LoRA URL (replicate/together)")
    parser.add_argument("--lora-id", default=None,
                        help="CivitAI LoRA ID (e.g. civitai:667086@746602). "
                             "Multiple: comma-separated with matching --lora-scale")
    parser.add_argument("--lora-scale", default="0.8",
                        help="LoRA strength, or comma-separated for multiple (e.g. '1.0,0.6')")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--aspect", default="9:16",
                        choices=["16:9","9:16","1:1","3:2","2:3","4:3","3:4"])
    parser.add_argument("--frames", type=int, default=81)
    parser.add_argument("--resolution", default="480p",
                        choices=["360p","480p","720p","1080p"])
    parser.add_argument("-m", "--model", default=None)
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--interactive", "-I", action="store_true")

    args = parser.parse_args()

    if args.list_models:
        print("Runware models:")
        from gen_lib.runware import MODELS as RW
        for k, v in RW.items():
            print(f"  {k:18s} {v['name']:35s} {v['price']}")
        print()
        print("Together AI models:")
        from gen_lib.together import MODELS as TG
        for k, v in TG.items():
            print(f"  {k:18s} {v['name']:35s} {v['price']}")
        print()
        print("OpenRouter models:")
        from gen_lib.openrouter import MODELS as OR
        for k, v in OR.items():
            print(f"  {k:18s} {v['name']:35s} {v['price']}")
        print()
        print("ModelsLab models:")
        from gen_lib.modelslab import MODELS as ML
        for k, v in ML.items():
            print(f"  {k:18s} {v['name']:35s} {v['price']}")
        sys.exit(0)

    if args.interactive or not args.prompt:
        from gen_lib.common import load_env
        print("=" * 60)
        print("🎨 gen.py — Interactive Image Generator")
        print("=" * 60)
        print()
        print("Platforms:")
        for i, p in enumerate(PLATFORM_DISPATCH, 1):
            print(f"  {i}. {p}")
        choice = input(f"\nPlatform [1-{len(PLATFORM_DISPATCH)}, default=1]: ").strip() or "1"
        platforms = list(PLATFORM_DISPATCH.keys())
        args.platform = platforms[int(choice) - 1] if choice.isdigit() else "runware"
        args.prompt = input("Prompt: ").strip()
        if not args.prompt:
            print("❌ Empty prompt")
            sys.exit(1)
        if args.platform in ("runware", "fal-pro", "grok", "replicate"):
            img = input("Reference image (empty=none): ").strip()
            if img:
                args.image = img
        if args.platform in ("runware", "together", "openrouter"):
            args.model = input(f"Model key [default=auto]: ").strip() or None
        print()

    # ── Build kwargs ─────────────────────────────────────────────────────
    kwargs = {"prompt": args.prompt, "negative_prompt": args.negative_prompt}

    if args.platform == "fal-pro":
        kwargs["image_path"] = args.image
        kwargs["strength"] = args.strength
        if args.seed is not None:
            kwargs["seed"] = args.seed
    elif args.platform == "fal-lora":
        kwargs["lora"] = True
    elif args.platform == "fal":
        if args.seed is not None:
            kwargs["seed"] = args.seed
    elif args.platform == "grok":
        if args.image:
            kwargs["image_path"] = args.image
    elif args.platform == "together":
        kwargs["model_key"] = args.model or "dreamshaper"
        if args.lora:
            kwargs["lora_url"] = args.lora
            kwargs["lora_scale"] = args.lora_scale
    elif args.platform == "replicate":
        kwargs["model_key"] = args.model or "flux"
        if args.image:
            kwargs["image_path"] = args.image
            kwargs["strength"] = args.strength
        if args.lora:
            kwargs["lora_url"] = args.lora
            kwargs["lora_scale"] = args.lora_scale
    elif args.platform == "openrouter":
        kwargs["model_key"] = args.model or "gemini-flash"
    elif args.platform == "runware":
        kwargs["model_key"] = args.model or "flux-dev"
        if args.image:
            kwargs["image_path"] = args.image
            kwargs["strength"] = args.strength
        if args.lora_id:
            kwargs["lora_id"] = args.lora_id
            kwargs["lora_scale"] = args.lora_scale
        if args.seed is not None:
            kwargs["seed"] = args.seed
        kwargs["aspect"] = args.aspect
    elif args.platform == "wan-nsfw":
        if args.image:
            kwargs["image_url"] = args.image
        kwargs["aspect_ratio"] = args.aspect
        kwargs["frames"] = args.frames
        kwargs["resolution"] = args.resolution
        kwargs["lora_strength"] = args.lora_scale
        if args.seed is not None:
            kwargs["seed"] = args.seed
    elif args.platform == "modelslab":
        kwargs["model_key"] = args.model or "pony"
        if args.seed is not None:
            kwargs["seed"] = args.seed
        if args.lora_id:
            kwargs["lora_model"] = args.lora_id
            kwargs["lora_strength"] = args.lora_scale

    # ── Dispatch ─────────────────────────────────────────────────────────
    t0 = time.time()
    module_path, func_name = PLATFORM_DISPATCH[args.platform]

    import importlib
    mod = importlib.import_module(module_path)
    func = getattr(mod, func_name)
    result = func(**kwargs)

    elapsed = time.time() - t0
    print(f"⏱️  {elapsed:.1f}s total")


if __name__ == "__main__":
    main()
