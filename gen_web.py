#!/usr/bin/env python3
"""gen_web.py — Web API wrapper for gen.py

Called by server.py /api/generate endpoint.
Reads JSON from stdin, runs gen functions safely (no sys.exit), writes JSON to stdout.
"""

import sys
import json
import os
import tempfile
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "workspace" / "scripts"))

_orig_exit = sys.exit
def _safe_exit(code=0):
    raise RuntimeError(f"gen.py called exit({code})")
sys.exit = _safe_exit

from gen_lib.common import load_env, OUTPUT_DIR

NSFW_MASTER_LORA = "civitai:667086@746602"

def result_ok(path=None, url=None, message="ok"):
    data = {"success": True, "message": message}
    if path:
        data["path"] = str(path)
        if path.exists():
            data["url"] = f"/api/output-images/{path.name}"
            data["size"] = path.stat().st_size
    if url:
        data["url"] = url
    return data

def result_err(msg):
    return {"success": False, "error": str(msg)}


def _generate_runware(args: dict) -> dict:
    """Runware path."""
    from gen_lib.runware import generate as gen_runware
    prompt = args.get("prompt", "").strip()
    negative = args.get("negative_prompt", "")
    model = args.get("model", "flux-dev")
    seed = args.get("seed")
    lora_id = args.get("lora_id")
    lora_scale = args.get("lora_scale", 0.8)
    cfg_scale = args.get("cfg_scale")  # float or None (None = use model default)
    nsfw_lora = args.get("nsfw_lora", False)
    nsfw = args.get("nsfw", model in ("pony-xl", "prefect-ill-xl"))

    # Qwen-Edit: save data URI to temp file
    _qwen_tmp = None
    raw_image = args.get("image_path", "")
    if raw_image:
        import tempfile, base64
        b64_data = raw_image.split(",")[-1] if "," in raw_image else raw_image
        _qwen_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        _qwen_tmp.write(base64.b64decode(b64_data))
        _qwen_tmp.close()

    if not prompt:
        return result_err("Prompt is required")

    effective_lora_id = lora_id
    effective_lora_scale = lora_scale

    if nsfw_lora and model == "flux-dev":
        if lora_id:
            effective_lora_id = f"{NSFW_MASTER_LORA},{lora_id}"
            effective_lora_scale = f"1.0,{lora_scale}"
        else:
            effective_lora_id = NSFW_MASTER_LORA
            effective_lora_scale = 1.0

    try:
        import time, io
        t0 = time.time()
        _old_stderr, _old_stdout = sys.stderr, sys.stdout
        _stderr_buf = io.StringIO()
        sys.stderr = sys.stdout = _stderr_buf
        try:
            result = gen_runware(prompt,
                model_key=model, negative_prompt=negative,
                lora_id=effective_lora_id, lora_scale=effective_lora_scale,
                seed=seed, image_path=_qwen_tmp.name if _qwen_tmp else None, cfg_scale=cfg_scale)
            if isinstance(result, tuple):
                result, used_seed = result
            else:
                used_seed = None
        finally:
            sys.stdout, sys.stderr = _old_stdout, _old_stderr
            _log = _stderr_buf.getvalue()
            _stderr_buf.close()

        elapsed = time.time() - t0
        resp = result_ok(path=result, message=f"Done in {elapsed:.1f}s")
        if result and result.exists():
            resp["size"] = result.stat().st_size
            resp["seed"] = used_seed
        return resp
    except Exception as e:
        return result_err(f"{type(e).__name__}: {e}\n{_log if '_log' in dir() else ''}")


def _generate_modelslab(args: dict) -> dict:
    """ModelsLab path — Pony, Illustrious, SDXL, FLUX.
    NSFW via rating_explicit for Pony/Illustrious, safety_checker off for all.
    """
    from gen_lib.modelslab import generate as gen_modelslab
    prompt = args.get("prompt", "").strip()
    negative = args.get("negative_prompt", "")
    model = args.get("model", "pony")
    seed = args.get("seed")
    lora_model = args.get("lora_model")
    lora_strength = float(args.get("lora_strength", 0.7))

    if not prompt:
        return result_err("Prompt is required")

    try:
        import time, io
        t0 = time.time()
        _old_stderr, _old_stdout = sys.stderr, sys.stdout
        _stderr_buf = io.StringIO()
        sys.stderr = sys.stdout = _stderr_buf
        try:
            result = gen_modelslab(prompt,
                model_key=model,
                negative_prompt=negative,
                seed=seed,
                lora_model=lora_model,
                lora_strength=lora_strength)
        finally:
            sys.stdout, sys.stderr = _old_stdout, _old_stderr
            _log = _stderr_buf.getvalue()
            _stderr_buf.close()

        elapsed = time.time() - t0
        resp = result_ok(path=result, message=f"Done in {elapsed:.1f}s")
        if result and result.exists():
            resp["size"] = result.stat().st_size
            resp["seed"] = used_seed
        return resp
    except Exception as e:
        return result_err(f"{type(e).__name__}: {e}\n{_log if '_log' in dir() else ''}")


def generate(args: dict) -> dict:
    """Dispatch to Runware or ModelsLab based on platform field."""
    load_env()  # ensure env vars loaded before platform dispatch
    platform = args.get("platform", "runware")
    if platform == "modelslab":
        return _generate_modelslab(args)
    return _generate_runware(args)


def list_loras(model: str = None) -> dict:
    """Return available LoRAs from registry, optionally filtered by base_model.
    Only returns LoRAs with runware_air_id (verified on Runware)."""
    import json as _json
    registry_path = Path(__file__).parent.parent / ".hermes" / "skills" / "creative" / "lora-manager" / "references" / "lora_registry.json"
    try:
        with open(registry_path) as f:
            registry = _json.load(f)
    except Exception:
        return {"success": False, "error": "Cannot read lora registry", "loras": []}

    all_loras = registry.get("loras", [])
    # Normalize model aliases for filtering
    model_aliases = {"pony-xl": "pony", "prefect-ill-xl": "illustrious"}
    match_base = model_aliases.get(model, model) if model else None
    result = []
    for l in all_loras:
        air_id = l.get("runware_air_id", "")
        if not air_id:
            continue  # skip prompt-only / unverified
        if match_base and l.get("base_model") != match_base:
            continue  # filter by model compatibility
        result.append({
            "id": l["id"],
            "name": l["name"],
            "air_id": air_id,
            "default_scale": l.get("default_scale", 0.8),
            "scale_range": l.get("scale_range", [0.1, 2.0]),
            "description": l.get("description", ""),
            "category": l.get("category", ""),
        })
    # Sort: concept(微调) > style > character/other
    CAT_PRIORITY = {"concept": 0, "style": 1}
    result.sort(key=lambda x: (CAT_PRIORITY.get(x["category"], 9), x["name"].lower()))
    return {"success": True, "loras": result}


def list_models(platform: str = "runware") -> dict:
    """Return available models for either Runware or ModelsLab."""
    if platform == "modelslab":
        from gen_lib.modelslab import MODELS as ML_MODELS
        models = [{"id": k, "name": v["name"], "price": v["price"]}
                  for k, v in ML_MODELS.items()]
        return {"success": True, "models": models}
    else:
        from gen_lib.runware import MODELS as RUNWARE_MODELS
        web_models = ["flux-dev", "pony-xl", "prefect-ill-xl", "qwen-edit"]
        models = [{"id": k, "name": RUNWARE_MODELS[k]["name"], "price": RUNWARE_MODELS[k]["price"]}
                  for k in web_models if k in RUNWARE_MODELS]
        return {"success": True, "models": models}


if __name__ == "__main__":
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"success": False, "error": "Invalid JSON"}))
        sys.exit(1)

    action = data.get("action", "generate")
    if action == "generate":
        result = generate(data)
    elif action == "list_models":
        result = list_models(data.get("platform", "runware"))
    elif action == "list_loras":
        result = list_loras(data.get("model"))
    else:
        result = {"success": False, "error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
