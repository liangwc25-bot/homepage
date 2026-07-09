#!/usr/bin/env python3
"""小默远程资源采集 — 被点儿 SSH 调用，输出 JSON"""
import json, os, time

d = {}

# CPU (0.5s 采样)
l1 = [int(x) for x in open('/proc/stat').readline().split()[1:]]
time.sleep(0.5)
l2 = [int(x) for x in open('/proc/stat').readline().split()[1:]]
td = sum(l2) - sum(l1)
cpu_pct = round((1 - (l2[3] - l1[3]) / td) * 100, 1) if td else 0
d['cpu'] = {'percent': cpu_pct, 'cores': os.cpu_count(),
    'load': [float(x) for x in open('/proc/loadavg').read().split()[:3]]}

# 内存
m = {}
for line in open('/proc/meminfo'):
    if ':' in line:
        k, v = line.split(':', 1)
        m[k.strip()] = int(v.strip().split()[0]) * 1024
mt = m.get('MemTotal', 0)
ma = m.get('MemAvailable', mt)
d['memory'] = {
    'total': mt, 'used': mt - ma,
    'percent': round((mt - ma) / mt * 100, 1) if mt else 0
}

# 磁盘
import subprocess
df = subprocess.check_output(['df', '-B1', '/']).decode().splitlines()[1].split()
d['disk'] = {
    'total': int(df[1]), 'used': int(df[2]),
    'percent': int(df[4].rstrip('%'))
}

# 运行时间
u = float(open('/proc/uptime').read().split()[0])
days, rem = divmod(u, 86400)
hours, rem = divmod(rem, 3600)
mins = rem // 60
d['uptime'] = f'{int(days)}天 {int(hours)}小时 {int(mins)}分钟'
d['hostname'] = 'vps67846'

# Docker 容器状态
try:
    r = subprocess.run(['docker', 'ps', '--format', '{{.Names}}|{{.Status}}'],
                       capture_output=True, text=True, timeout=5)
    containers = []
    for line in r.stdout.strip().splitlines():
        if '|' in line:
            name, status = line.split('|', 1)
            containers.append({'name': name, 'status': status.split()[0]})
    d['containers'] = containers
except Exception:
    d['containers'] = []

print(json.dumps(d))
