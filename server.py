#!/usr/bin/env python3
"""点儿的主页后端 — 静态文件 + API + 双机实时资源"""
import json, os, time, subprocess, threading, uuid
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


# Job tracking for async generation
JOBS = {}  # {job_id: {"status": "running"|"done", "result": ..., "proc": Popen}}


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
        if self._parsed_path == "/api/list-loras":
            return self._handle_list_loras()
        if self._parsed_path == "/api/list-models":
            return self._handle_list_models()
        if self._parsed_path.startswith("/api/output-images/"):
            return self._handle_output_image()
        if self._parsed_path.startswith("/api/job"):
            return self._handle_job()
        self.directory = str(STATIC_DIR)
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/generate":
            return self._handle_generate()
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not found")

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _handle_status(self):
        data = {}
        # DeepSeek
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
        # OpenRouter
        if OPENROUTER_KEY:
            try:
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    key_data = json.loads(r.read()).get("data", {})
                # Also fetch credits
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
        # Runware
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
        self._json_response(data)

    def _handle_resources(self):
        pixel = _get_pixel_resources()
        moris = _get_moris_resources()
        self._json_response({"pixel": pixel, "moris": moris, "ts": int(time.time())})

    def _handle_list_loras(self):
        """List available LoRAs from registry, filtered by model."""
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        model = qs.get("model", [None])[0]
        script_path = STATIC_DIR / "gen_web.py"
        try:
            r = subprocess.run(
                ["python3", str(script_path)],
                input=json.dumps({"action": "list_loras", "model": model}),
                capture_output=True, text=True, timeout=15,
            )
            result = json.loads(r.stdout.strip())
        except Exception as e:
            result = {"success": False, "error": str(e), "loras": []}
        self._json_response(result)

    def _handle_list_models(self):
        """List available Runware models."""
        script_path = STATIC_DIR / "gen_web.py"
        try:
            r = subprocess.run(
                ["python3", str(script_path)],
                input=json.dumps({"action": "list_models", "platform": "runware"}),
                capture_output=True, text=True, timeout=15,
            )
            result = json.loads(r.stdout.strip())
        except Exception as e:
            result = {"success": False, "error": str(e), "models": []}
        self._json_response(result)

    def _handle_output_image(self):
        """Serve generated images from output directory."""
        filename = self.path.split("/api/output-images/", 1)[1]
        output_dir = Path.home() / "workspace" / "output" / "images"
        filepath = output_dir / filename
        if not filepath.exists() or not filepath.is_file():
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return
        ext = filepath.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".mp4": "video/mp4",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(filepath.read_bytes())

    def _handle_generate(self):
        """Handle image generation — spawn bg process, return job_id immediately."""
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json_response({"success": False, "error": "Invalid JSON"}, 400)

        data["action"] = "generate"
        job_id = uuid.uuid4().hex[:8]
        script_path = STATIC_DIR / "gen_web.py"

        proc = subprocess.Popen(
            ["python3", str(script_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        proc.stdin.write(json.dumps(data))
        proc.stdin.close()

        JOBS[job_id] = {"status": "running", "result": None, "proc": proc}

        # Background thread to wait for completion
        def _await():
            try:
                proc.wait(timeout=300)
                stdout = proc.stdout.read()
                try:
                    result = json.loads(stdout.strip())
                except json.JSONDecodeError:
                    result = {"success": False, "error": f"Output invalid: {stdout[:300]}"}
                JOBS[job_id]["result"] = result
                JOBS[job_id]["status"] = "done"
                JOBS[job_id].pop("proc", None)
            except subprocess.TimeoutExpired:
                proc.kill()
                JOBS[job_id]["result"] = {"success": False, "error": "Timed out (300s)"}
                JOBS[job_id]["status"] = "done"
                JOBS[job_id].pop("proc", None)
            except Exception as e:
                JOBS[job_id]["result"] = {"success": False, "error": str(e)}
                JOBS[job_id]["status"] = "done"
                JOBS[job_id].pop("proc", None)

        threading.Thread(target=_await, daemon=True).start()

        self._json_response({"success": True, "job_id": job_id, "status": "queued"})

    def _handle_job(self):
        """Check async generation job status."""
        job_id = self.path.split("/api/job?job=", 1)[-1].split("&")[0] if "?" in self.path else ""
        if not job_id:
            return self._json_response({"error": "Missing job_id"}, 400)
        job = JOBS.get(job_id)
        if not job:
            return self._json_response({"error": "Job not found"}, 404)
        if job["status"] == "done":
            return self._json_response({"job_id": job_id, "status": "done", "result": job["result"]})
        return self._json_response({"job_id": job_id, "status": "running"})

# ── 启动 ──

if __name__ == "__main__":
    port = 8088
    server = HTTPServer(("127.0.0.1", port), HomepageHandler)
    print(f"点儿主页 → http://0.0.0.0:{port}")
    server.serve_forever()
