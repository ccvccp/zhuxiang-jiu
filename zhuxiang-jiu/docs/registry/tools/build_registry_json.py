# -*- coding: utf-8 -*-
"""竹盾 T0.6: registry.json 生成器(临时工具——P1 起由 zhudun index 替代并移除)

铁律: MD 为源、JSON 由脚本生成(README 规则 1)——本脚本只解析四份 MD, 禁止手工编辑 JSON。
产出: docs/registry/registry.json
  - endpoints   (L0-端点清单: module/method/path/func/line/auth/gated)
  - switches    (L0-模块清单: 运行时+附属域, key/default/vocab/rollback/loc/domain)
  - modules     (L0-模块清单: services/core/repositories/scripts 明细)
  - pages/js_modules/pages_core (L0-页面清单)
  - red_lines/behavior_assets (红线标记: 文件路径抽取——zhudun impact 命中判定用)
用法: python docs/registry/tools/build_registry_json.py
"""
import io
import json
import os
import re
import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REG = os.path.join(REPO, "docs", "registry")
OUT = os.path.join(REG, "registry.json")

EP_ROW = re.compile(r'^\|\s*(GET|POST|PUT|DELETE|PATCH|WS)\s*\|\s*`([^`]+)`\s*\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$')
EP_MOD = re.compile(r'^###\s+(\S+?)(?:\s+—\s+|\s*$)')
SW_ROW = re.compile(r'^\|\s*`([A-Z][A-Z0-9_]+)`\s*\|([^|]*)\|([^|]*)\|([^|]*)\|\s*([^:|]+):(\d+)\s*\|([^|]*)\|\s*$')
MOD_ROW = re.compile(r'^\|\s*([\w.\-]+\.(?:py|js))\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$')
PAGE_ROW = re.compile(r'^\|\s*([\w.\-]+\.html)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$')
JS_ROW = re.compile(r'^\|\s*(js/[\w.\-/]+\.js)\s*\|\s*([^|]*?)\s*\|\s*$')
CORE_ROW = re.compile(r'^\|\s*([\w.\-]+\.html)\s*\|\s*(仓库根|taro-app/public|deploy/|⚠[^|]*)\s*\|\s*(✅|⚠[^|]*)\s*\|\s*$')
RED_H = re.compile(r'^##\s+(R\d)\s+(.*)$')
RED_LOC = re.compile(r'\*\*(?:位置|代码位置)\*\*[:：]')
PATH_RE = re.compile(r'([\w.\-/]+\.(?:py|js|sh|ps1|conf|html|md))')
B_ROW = re.compile(r'^\|\s*(B\d)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$')


def read(name):
    return io.open(os.path.join(REG, name), encoding="utf-8").read().split("\n")


def parse_endpoints():
    out, mod = [], None
    for line in read("L0-端点清单.md"):
        m = EP_MOD.match(line)
        if m and m.group(1).endswith(".py"):
            mod = m.group(1)
            continue
        m = EP_ROW.match(line)
        if m and mod:
            out.append({"module": mod, "method": m.group(1), "path": m.group(2),
                        "func": m.group(3), "line": int(m.group(4)),
                        "auth": m.group(5).strip(), "gated": m.group(6).strip() != "-"})
    return out


def parse_modules_md():
    switches, modules, group = [], [], None
    in_sw = False
    for line in read("L0-模块清单.md"):
        if line.startswith("### services/"):
            group = "services"
        elif line.startswith("### core/"):
            group = "core"
        elif line.startswith("### repositories/"):
            group = "repositories"
        elif line.startswith("### scripts/"):
            group = "scripts"
        elif line.startswith("## ") and "开关" not in line:
            if not line.startswith("## 环境开关"):
                group = None if "明细" not in line else group
        if line.startswith("### 运行时开关") or line.startswith("### 附属域开关"):
            in_sw = True
            group = None
            continue
        if line.startswith("## "):
            in_sw = False
        if in_sw:
            m = SW_ROW.match(line)
            if m:
                dom = m.group(7).strip()
                switches.append({"key": m.group(1), "default": m.group(2).strip(),
                                  "vocab": m.group(3).strip(), "rollback": m.group(4).strip(),
                                  "loc": "%s:%s" % (m.group(5), m.group(6)), "domain": dom})
            continue
        if group:
            m = MOD_ROW.match(line)
            if m and m.group(1) != "文件":
                modules.append({"group": group, "file": m.group(1),
                                "category": m.group(2).strip(), "desc": m.group(3).strip()})
    return switches, modules


def parse_pages_md():
    pages, js_mods, core_pages = [], [], []
    section = None
    for line in read("L0-页面清单.md"):
        if line.startswith("## 根页面明细"):
            section = "pages"
        elif line.startswith("## js/"):
            section = "js"
        elif line.startswith("## 三向对照"):
            section = None
        elif line.startswith("**核心九页来源对照"):
            section = "core"
        if section == "pages":
            m = PAGE_ROW.match(line)
            if m and m.group(1) != "文件":
                pages.append({"file": m.group(1), "category": m.group(2), "title": m.group(3)})
        elif section == "js":
            m = JS_ROW.match(line)
            if m:
                js_mods.append({"file": m.group(1), "category": m.group(2).strip()})
        elif section == "core":
            m = CORE_ROW.match(line)
            if m:
                core_pages.append({"page": m.group(1), "local_src": m.group(2), "dist": m.group(3)})
    return pages, js_mods, core_pages


def norm_paths(paths):
    """归一化: 去 MD 链接 ../../ 相对前缀, 滤通配片段"""
    out = set()
    for p in paths:
        p = p.replace("../../", "")
        if "*" in p or not p or p.startswith("_"):
            continue
        out.add(p)
    return sorted(out)


def parse_redlines():
    reds, behs, cur = [], [], None
    for line in read("红线标记.md"):
        m = RED_H.match(line)
        if m:
            cur = {"id": m.group(1), "name": m.group(2).strip(), "files": []}
            reds.append(cur)
            continue
        if cur and RED_LOC.search(line):
            cur["files"] = norm_paths(PATH_RE.findall(line))
            cur = None
        m = B_ROW.match(line)
        if m and m.group(1).startswith("B"):
            behs.append({"id": m.group(1), "item": m.group(2).strip(),
                         "invariant": m.group(3).strip(),
                         "files": norm_paths(PATH_RE.findall(m.group(4)))})
    return reds, behs


def main():
    endpoints = parse_endpoints()
    switches, modules = parse_modules_md()
    pages, js_mods, core_pages = parse_pages_md()
    reds, behs = parse_redlines()

    data = {
        "_meta": {
            "generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "policy": "MD为源,脚本生成,禁手工编辑(README 规则1); 刷新=重跑 tools/build_registry_json.py",
            "sources": ["L0-端点清单.md", "L0-模块清单.md", "L0-页面清单.md", "红线标记.md"],
        },
        "stats": {
            "endpoints": len(endpoints),
            "gated_endpoints": sum(1 for e in endpoints if e["gated"]),
            "switches": len(switches),
            "modules": len(modules),
            "pages": len(pages),
            "js_modules": len(js_mods),
            "red_lines": len(reds),
            "behavior_assets": len(behs),
        },
        "endpoints": endpoints,
        "switches": switches,
        "modules": modules,
        "pages": pages,
        "js_modules": js_mods,
        "pages_core_PAGED": core_pages,
        "red_lines": reds,
        "behavior_assets": behs,
    }
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(data, ensure_ascii=False, indent=1))
    # 自检: JSON 可解析(验收)
    json.load(io.open(OUT, encoding="utf-8"))
    print("OK endpoints=%d gated=%d switches=%d modules=%d pages=%d js=%d core=%d reds=%d behs=%d" % (
        len(endpoints), data["stats"]["gated_endpoints"], len(switches), len(modules),
        len(pages), len(js_mods), len(core_pages), len(reds), len(behs)))


if __name__ == "__main__":
    main()
