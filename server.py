#!/usr/bin/env python3
"""点儿的主页后端 — 静态文件 + API + 双机实时资源"""
import json, os, time, subprocess
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
STATIC_DIR = Path(__file__).parent
MORIS_SSH = ["ssh", "-p", "37980", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "root@100.73.239.42"]


# ── 资源采集 ──

def _get_pixel_resources():
    """点儿本机资源 — psutil"""
    cpu_pct = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    boot = psutil.boot_time()
    uptime_s = time.time() - boot
    days, rem = divmod(uptime_s, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    uptime_str = f"{int(days)}天 {int(hours)}小时 {int(mins)}分钟"

    # Docker 容器状态
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
        "cpu": {
            "percent": round(cpu_pct, 1),
            "cores": psutil.cpu_count(),
            "load": [round(x, 2) for x in os.getloadavg()],
        },
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "percent": round(mem.percent, 1),
        },
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "percent": disk.percent,
        },
        "uptime": uptime_str,
        "containers": containers,
    }


def _get_moris_resources():
    """SSH 到小默采集资源"""
    try:
        result = subprocess.run(
            MORIS_SSH + ["python3", "/tmp/remote_collect.py"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
        return {"error": result.stderr.strip() or "empty output"}
    except subprocess.TimeoutExpired:
        return {"error": "SSH 超时"}
    except Exception as e:
        return {"error": str(e)}


# ── HTTP 处理 ──

class HomepageHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse
        self._parsed_path = urlparse(self.path).path
        if self._parsed_path == "/api/status":
            return self._handle_status()
        if self._parsed_path == "/api/resources":
            return self._handle_resources()
        self.directory = str(STATIC_DIR)
        return super().do_GET()


# ── 启动 ──

if __name__ == "__main__":
    port = 8088
    server = HTTPServer(("127.0.0.1", port), HomepageHandler)
    print(f"点儿主页 → http://127.0.0.1:{port}")
    server.serve_forever()
