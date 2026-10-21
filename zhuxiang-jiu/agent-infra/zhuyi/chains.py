"""zhuyi chains: 业务链定义 MD 解析器（79号·T0.5）

docs/registry/业务链定义.md → 结构化链数据（6 链 × 有序步骤 × 端点）。
供 audit 三检消费: T1.1 端点在册 / T1.2 鉴权可达 / T1.4 数据流衔接。
对齐口径: 端点 (method, path) 与 registry.json endpoints 匹配——
path 模板形态一致（{order_id}）, 精确字符串比对; 非标准形态（"系列"/"等"/
"或 X 系"）归 ambiguous, 用子串匹配降级对齐; (内部) 步骤与 audit 待核
标记单独归类, 不参与端点对齐。
"""
import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHAINS_MD = os.path.join(REPO_ROOT, "docs", "registry", "业务链定义.md")
REGISTRY_JSON = os.path.join(REPO_ROOT, "docs", "registry", "registry.json")

_EP_RE = re.compile(r"(GET|POST|PUT|DELETE|PATCH)\s+(/[^\s|（()（]+)")


def split_endpoints(text):
    """端点列文本 → [(method, path, ambiguous)] + internal/pending 标记。

    形态覆盖: "POST /api/x" 精确; "·" 压缩(…/confirm·close·complete)展开;
    "或"多写并存; "系列/等/系" 尾缀 → ambiguous 子串匹配降级。
    """
    eps = []
    internal = "(内部)" in text or "(复用" in text
    pending = bool(re.search(r"\[audit-\d[^\]]*\]", text))
    for m in _EP_RE.finditer(text):
        method, path = m.group(1), m.group(2)
        rest = text[m.end():]
        ambiguous = rest.lstrip().startswith(("系", "等")) or "或" in rest
        if "·" in path:
            head, _, tails = path.rpartition("/")
            for t in tails.split("·"):
                eps.append((method, head + "/" + t, False))
        else:
            eps.append((method, path.rstrip("。；;"), ambiguous))
    return eps, internal, pending


def parse_chains(md_path=CHAINS_MD):
    """业务链定义 MD → [ {name, goal, steps:[{no,label,endpoints,
    internal,pending,pre,fields}] } ]；仅收步骤表列齐(5 列)的数据行。"""
    with open(md_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    chains, cur, in_steps = [], None, False
    for ln in lines:
        h = re.match(r"## 链[一二三四五六]：([^（(]+)", ln.strip())
        if h:
            cur = {"name": h.group(1).strip(), "goal": "", "steps": []}
            chains.append(cur)
            in_steps = False
            continue
        if cur is None:
            continue
        g = re.match(r"\*\*业务目标\*\*[:：]?\s*(.+)", ln.strip())
        if g:
            cur["goal"] = g.group(1).strip()
        if ln.strip().startswith("| #") and "步骤" in ln:
            in_steps = True
            continue
        if in_steps and ln.strip().startswith("|"):
            cols = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cols) != 5 or not cols[0].isdigit():
                continue
            eps, internal, pending = split_endpoints(cols[2])
            cur["steps"].append({
                "no": int(cols[0]), "label": cols[1],
                "endpoints": eps, "internal": internal, "pending": pending,
                "pre": cols[3], "fields": cols[4],
            })
        elif in_steps and not ln.strip().startswith("|"):
            in_steps = False
    return chains


def load_registry(registry_path=REGISTRY_JSON):
    with open(registry_path, encoding="utf-8") as fh:
        return json.load(fh)


def align_registry(chains, registry):
    """链步骤端点 × registry.endpoints → 对齐报告。

    每端点: hit(精确 method+path) / partial(子串, ambiguous 降级) /
    miss(未命中)。internal 步骤不计。返回 {链名: {步: {...}}} + 汇总。
    """
    index = {(e["method"], e["path"]): e for e in registry["endpoints"]}
    paths = [e["path"] for e in registry["endpoints"]]
    report, total = {}, {"hit": 0, "partial": 0, "miss": 0, "internal": 0}
    for chain in chains:
        c_rep = {}
        for step in chain["steps"]:
            if not step["endpoints"]:
                if step["internal"]:
                    total["internal"] += 1
                c_rep[step["no"]] = {"internal": step["internal"],
                                      "results": []}
                continue
            results = []
            for method, path, ambiguous in step["endpoints"]:
                if (method, path) in index:
                    results.append(("hit", method, path))
                    total["hit"] += 1
                elif ambiguous and any(path in p for p in paths):
                    results.append(("partial", method, path))
                    total["partial"] += 1
                else:
                    results.append(("miss", method, path))
                    total["miss"] += 1
            c_rep[step["no"]] = {"internal": step["internal"], "results": results}
        report[chain["name"]] = c_rep
    return report, total


def run(_args=None):
    """解析自检入口: 6 链全解析 + 对齐汇总（M0 抽检口径 6/6 用）。"""
    chains = parse_chains()
    registry = load_registry()
    _, total = align_registry(chains, registry)
    step_sum = sum(len(c["steps"]) for c in chains)
    print(f"[zhuyi chains] 链={len(chains)} 步骤={step_sum} "
          f"对齐 hit={total['hit']} partial={total['partial']} "
          f"miss={total['miss']} internal={total['internal']}")
    for c in chains:
        print(f"  - {c['name']}: {len(c['steps'])} 步")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
