"""zhudun rules——检查清单映射（T1.6）

改动面 → 进化检查清单提醒项（对应 [docs/进化检查清单.md](../../docs/进化检查清单.md) A-F 节）:
  B1 改 js/css → 全站 v= 升位（45 处无版本号引用的历史教训）
  C3 改动含 Redis 写操作 → 改键前备份旧值
  C4 改动含绝对 URL → 资源一律 /images/ 相对路径
  E  涉部署面(deploy//conf/nginx) → tar 相对路径+覆盖式; 改 nginx 先取实时 conf
  F1 改 py/js → 改后语法检查
提醒性质: 启发式内容检测, 宁可误报不可漏报, 人工复核定取舍。
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

REDIS_WRITE_RE = re.compile(
    r"\b(hset|hdel|set|setex|delete|del|expire|rpush|lpush|sadd|srem|zadd|"
    r"incr|decr|lset)\s*\(")
ABS_URL_RE = re.compile(r"https?://")


def _read_head(path, limit=200000):
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def checklist_map(changed, repo=DEFAULT_REPO):
    """改动文件 → 检查清单提醒项映射 {file: [条目]}（无提醒的文件不出现）"""
    out = {}
    for f in changed:
        norm = f.replace("\\", "/")
        ext = os.path.splitext(norm)[1].lower()
        items = []
        src = ""
        if ext in (".js", ".css", ".py", ".html"):
            src = _read_head(os.path.join(repo, f))
        # B1: 生产前端资产版本升位（js/css + zhuxiang-jiu 下 html）
        if ext in (".js", ".css") or (ext == ".html" and "zhuxiang-jiu/" in norm):
            items.append("B1 改 js/css 后全站 v= 升位(html 引用须带新 ?v=)")
        # C3: Redis 写操作备份（backend py）
        if ext == ".py" and "backend" in norm.split("/") and REDIS_WRITE_RE.search(src):
            items.append("C3 检测到 Redis 写操作——改键前备份旧值(/tmp bak)")
        # C4: 绝对 URL 扫描
        if ext in (".js", ".html", ".py") and ABS_URL_RE.search(src):
            items.append("C4 检测到绝对 URL——资源一律 /images/ 相对路径(未备案侧必拦)")
        # E: 部署面（含仓库根级相对路径 deploy/ 与任意层 /deploy/）
        if "/deploy/" in norm or norm.startswith("deploy/") \
                or ext == ".conf" or "nginx" in os.path.basename(norm).lower():
            items.append("E 部署面: tar 相对路径前缀+覆盖式部署; 改 nginx 先取服务器实时 conf")
        # F1: 语法检查
        if ext in (".py", ".js"):
            items.append("F1 改动后跑语法检查(python import / node --check)")
        if items:
            out[f] = items
    return out
