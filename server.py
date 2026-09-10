#!/usr/bin/env python3
"""点儿的主页后端 — 静态文件 + API + 双机实时资源"""
import json, os, time, subprocess, re
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import psutil

# ── 配置 ──
_env_file = Path(__file__).parent.parent / ".hermes" / ".env"
_deepseek_key = ""
_openrouter_key = ""
_runware_key = ""
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            _deepseek_key = line.split("=", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("OPENROUTER_API_KEY="):
            _openrouter_key = line.split("=", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("RUNWARE_API_KEY="):
            _runware_key = line.split("=", 1)[1].strip().strip('"').strip("'")
DEEPSEEK_KEY = _deepseek_key or os.environ.get("DEEPSEEK_API_KEY", "")
OPENROUTER_KEY = _openrouter_key or os.environ.get("OPENROUTER_API_KEY", "")
RUNWARE_KEY = _runware_key or os.environ.get("RUNWARE_API_KEY", "")

# Replicate balance via web cookie (replicate.com/api/users/<user>/unused-credit)
_REPLICATE_COOKIE_FILE = Path("/root/workspace/creds/replicate_cookies.txt")
_REPLICATE_USERNAME = "liangwc25-bot"
def _replicate_cookie():
    try:
        lines = _REPLICATE_COOKIE_FILE.read_text().splitlines()
    except Exception:
        return ""
    parts = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or len(line.split()) < 7:
            continue
        p = line.split()
        parts.append(f"{p[5]}={p[6]}")
    return "; ".join(parts)

STATIC_DIR = Path(__file__).parent
MORIS_SSH = ["ssh", "-p", "37980", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "root@100.73.239.42"]


# ── 资源采集 ──

def _get_pixel_resources():
    cpu_pct = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    boot = psutil.boot_time()
    uptime_s = time.time() - boot
    days, rem = divmod(uptime_s, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    uptime_str = f"{int(days)}天 {int(hours)}小时 {int(mins)}分钟"
    containers = []
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}|{{.Status}}"],
                           capture_output=True, text=True, timeout=5)
        for line in r.stdout.strip().splitlines():
            if "|" in line:
                name, status = line.split("|", 1)
                containers.append({"name": name, "status": status.split()[0]})
    except Exception:
        pass
    return {
        "hostname": "vps67480",
        "cpu": {"percent": round(cpu_pct, 1), "cores": psutil.cpu_count(),
                "load": [round(x, 2) for x in os.getloadavg()]},
        "memory": {"total": mem.total, "used": mem.used, "percent": round(mem.percent, 1)},
        "disk": {"total": disk.total, "used": disk.used, "percent": disk.percent},
        "uptime": uptime_str, "containers": containers,
    }

def _get_moris_resources():
    try:
        result = subprocess.run(
            MORIS_SSH + ["python3", "/root/scripts/remote_collect.py"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        return {"error": result.stderr.strip() or "empty output"}
    except subprocess.TimeoutExpired:
        return {"error": "SSH 超时"}
    except Exception as e:
        return {"error": str(e)}


# ── R2 用量（rclone size，缓存避免频繁列对象）──
_R2_CACHE = {"ts": 0.0, "data": None}
_R2_CACHE_TTL = 600  # 秒 = 10 分钟。变化缓慢, 页面加载时查一次即可; 打开时若距上次<10min直接用缓存

def _get_r2_usage():
    now = time.time()
    if _R2_CACHE["data"] and (now - _R2_CACHE["ts"]) < _R2_CACHE_TTL:
        return _R2_CACHE["data"]
    try:
        r = subprocess.run(["rclone", "size", "r2:"],
                           capture_output=True, text=True, timeout=20)
        out = r.stdout
        m_obj = re.search(r"Total objects:\s*(\d+)", out)
        m_size = re.search(r"Total size:.*?\((\d+)\s*Byte\)", out)
        data = {
            "objects": int(m_obj.group(1)) if m_obj else None,
            "bytes": int(m_size.group(1)) if m_size else None,
        }
        if r.returncode != 0 or data["bytes"] is None:
            data = {"error": (r.stderr or out or "rclone size 失败").strip()}
    except Exception as e:
        data = {"error": str(e)}
    _R2_CACHE["ts"] = now
    _R2_CACHE["data"] = data
    return data


# ── HTTP 处理 ──

class HomepageHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse
        self._parsed_path = urlparse(self.path).path
        if self._parsed_path == "/api/status":
            return self._handle_status()
        if self._parsed_path == "/api/resources":
            return self._handle_resources()
        if self._parsed_path == "/api/r2":
            return self._handle_r2()
        self.directory = str(STATIC_DIR)
        return super().do_GET()

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _handle_status(self):
        data = {}
        if DEEPSEEK_KEY:
            try:
                req = urllib.request.Request(
                    "https://api.deepseek.com/user/balance",
                    headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    data["deepseek"] = json.loads(r.read())
            except Exception as e:
                data["deepseek"] = {"error": str(e)}
        else:
            data["deepseek"] = {"error": "No DEEPSEEK_API_KEY"}
        if OPENROUTER_KEY:
            try:
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    key_data = json.loads(r.read()).get("data", {})
                try:
                    req2 = urllib.request.Request(
                        "https://openrouter.ai/api/v1/credits",
                        headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                    )
                    with urllib.request.urlopen(req2, timeout=10) as r2:
                        credits_data = json.loads(r2.read()).get("data", {})
                except Exception:
                    credits_data = {}
                data["openrouter"] = {
                    "usage": key_data.get("usage"),
                    "usage_monthly": key_data.get("usage_monthly"),
                    "is_free_tier": key_data.get("is_free_tier"),
                    "total_credits": credits_data.get("total_credits"),
                    "total_usage": credits_data.get("total_usage"),
                }
            except Exception as e:
                data["openrouter"] = {"error": str(e)}
        else:
            data["openrouter"] = {"error": "No OPENROUTER_API_KEY"}
        if RUNWARE_KEY:
            try:
                import uuid
                payload = json.dumps([{
                    "taskType": "accountManagement",
                    "taskUUID": str(uuid.uuid4()),
                    "operation": "getDetails",
                }]).encode()
                req = urllib.request.Request(
                    "https://api.runware.ai/v1/accountManagement",
                    data=payload,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {RUNWARE_KEY}"},
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    resp = json.loads(r.read())
                    acct = resp["data"][0]
                    data["runware"] = {
                        "balance": acct["balance"],
                        "total_usage": acct["usage"]["total"]["credits"],
                        "today_usage": acct["usage"]["today"]["credits"],
                        "today_requests": acct["usage"]["today"]["requests"],
                        "total_requests": acct["usage"]["total"]["requests"],
                    }
            except Exception as e:
                data["runware"] = {"error": str(e)}
        else:
            data["runware"] = {"error": "No RUNWARE_API_KEY"}
        # Replicate (unused credit via web cookie)
        rep_cookie = _replicate_cookie()
        if rep_cookie:
            try:
                req = urllib.request.Request(
                    f"https://replicate.com/api/users/{_REPLICATE_USERNAME}/unused-credit",
                    headers={"User-Agent": "Mozilla/5.0", "Cookie": rep_cookie},
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    data["replicate"] = json.loads(r.read())
            except Exception as e:
                data["replicate"] = {"error": str(e)}
        else:
            data["replicate"] = {"error": "No replicate cookie"}
        self._json_response(data)

    def _handle_resources(self):
        pixel = _get_pixel_resources()
        moris = _get_moris_resources()
        self._json_response({"pixel": pixel, "moris": moris, "ts": int(time.time())})

    def _handle_r2(self):
        self._json_response(_get_r2_usage())

# ── 启动 ──

if __name__ == "__main__":
    port = 8088
    server = HTTPServer(("127.0.0.1", port), HomepageHandler)
    print(f"点儿主页 → http://127.0.0.1:{port}")
    server.serve_forever()
