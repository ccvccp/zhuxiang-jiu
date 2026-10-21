"""zhuyi audit: L1 业务链完整性审查（79号·P1, 当前实现 T1.1/T1.2/T1.3）

第一检(T1.1) 端点在册: 链步骤端点 × registry 存在性, miss=断链候选。
第二检(T1.2) 鉴权可达: 链步骤鉴权期望(前置条件列) × registry 端点实际 auth 面——
  期望公开但实际登录·JWT → auth_401(无登录态走链被拦, qr 死条目事故形态);
  期望登录但实际公开 → auth_exposed(过度暴露风险, 非断链)。
  **qr benchmark 核心**: build_qr_dead_registry 构造 e73f6800 修复前死条目
  时代 registry 副本 → check_auth_reachable 必须检出扫码链 401 永拦。
第三检(T1.3) 前端调用点: 链步骤端点 × 主站前端(仓库根 html + js/*.js)文本
  扫描(复用竹盾反向引用思路的文件面)——无参端点=完整路径子串; 带参端点=首段
  子串+尾段带引号字面量(拼接写法 `'/api/x/' + id + '/scan'`); no_call=主站
  前端无调用点(调用可能在 taro 端/服务间, 如实报出供人解读, 非必然断链)。
后续任务逐检扩展: T1.5 断链六段式报告。
第四检(T1.4) 数据流衔接: 链内衔接字段列(入→出)的关键 ID 抽验——identifier
  类入字段(_id/pay_no/JWT等)须可溯源至前序步骤产出(宽匹配, 豁免链首步外部
  输入); 未溯源 → dataflow_gap(外部输入或链定义缺字段, 供人工确认——
  audit-1 升档触发点/audit-2 结算端分工取证点在此口径下核对)。
"""
import os
import re

try:
    from zhuyi import chains as chains_mod
except ImportError:
    import chains as chains_mod

_AUTH_EXPECT_RE = re.compile(r"(公开·[A-Za-z·]+|登录·JWT|游客)")


def check_endpoint_registry(chs=None, registry=None):
    """第一检: 端点在册。返回 (findings, total)。

    findings: 非 hit 项明细 [{chain, step, label, method, path, kind}],
    kind=miss(不在册)/partial(模糊降级)。total: 对齐汇总(对齐 chains 口径)。
    """
    chs = chs if chs is not None else chains_mod.parse_chains()
    registry = registry if registry is not None else chains_mod.load_registry()
    rep, total = chains_mod.align_registry(chs, registry)
    findings = []
    for chain in chs:
        for step in chain["steps"]:
            for kind, method, path in rep[chain["name"]][step["no"]]["results"]:
                if kind != "hit":
                    findings.append({
                        "chain": chain["name"], "step": step["no"],
                        "label": step["label"], "method": method,
                        "path": path, "kind": kind,
                    })
    return findings, total


def _expected_auth(pre):
    """从前置条件列提取鉴权期望: 公开·*→公开 / 登录·JWT→登录 / 游客→公开; 无→None"""
    m = _AUTH_EXPECT_RE.search(pre)
    if not m:
        return None
    token = m.group(1)
    return "登录" if token == "登录·JWT" else "公开"


def check_auth_reachable(chs=None, registry=None):
    """第二检: 鉴权可达。链步骤鉴权期望 × registry 端点实际 auth 面。

    仅检在册端点（不在册已由第一检报出）; 前置无鉴权词的步骤跳过（纯业务前置）。
    返回 findings: [{chain, step, label, method, path, kind, expect, actual}]。
    """
    chs = chs if chs is not None else chains_mod.parse_chains()
    registry = registry if registry is not None else chains_mod.load_registry()
    index = {(e["method"], e["path"]): e for e in registry["endpoints"]}
    findings = []
    for chain in chs:
        for step in chain["steps"]:
            expect = _expected_auth(step["pre"])
            if expect is None:
                continue
            for method, path, _amb in step["endpoints"]:
                ep = index.get((method, path))
                if ep is None:
                    continue
                actual = ep["auth"]
                if expect == "公开" and actual.startswith("登录"):
                    findings.append({
                        "chain": chain["name"], "step": step["no"],
                        "label": step["label"], "method": method, "path": path,
                        "kind": "auth_401", "expect": expect, "actual": actual,
                    })
                elif expect == "登录" and actual.startswith("公开"):
                    findings.append({
                        "chain": chain["name"], "step": step["no"],
                        "label": step["label"], "method": method, "path": path,
                        "kind": "auth_exposed", "expect": expect, "actual": actual,
                    })
    return findings


def build_qr_dead_registry(registry=None):
    """qr benchmark fixture: e73f6800 修复前死条目时代的 registry 副本。

    scan/exchange/cancel 鉴权=登录·JWT——三条 EXACT 无参死条目永不匹配带参路由
    的实际拦截效果（案例 C-20261021-01 实况）。M1 回放: 用此 fixture 跑
    check_auth_reachable 必须检出扫码登录链 401 永拦。
    """
    registry = registry if registry is not None else chains_mod.load_registry()
    dead = {"endpoints": [dict(e) for e in registry["endpoints"]]}
    for e in dead["endpoints"]:
        if (e["method"] == "POST" and e["path"].startswith("/api/entry/qr/")
                and e["path"].endswith(("/scan", "/exchange", "/cancel"))):
            e["auth"] = "登录·JWT"
    return dead


def _call_tokens(path):
    """端点 path → (首段, 尾段|None)。

    /api/product/list → (完整路径, None)——纯子串匹配(注释/代码均算证据面);
    /api/product/{id} → ('/api/product/', None);
    /api/entry/qr/{qr_id}/scan → ('/api/entry/qr/', '/scan')——尾段匹配须带
    引号字面量(拼接写法 `+ '/scan'`), 防 '/pay' 前缀误命中 '/payment/'。
    """
    if "{" not in path:
        return (path, None)
    head = path.split("{")[0]
    tail = path.split("}")[-1]
    return (head, tail or None)


def _frontend_texts(repo_root=None):
    """主站前端扫描面: [(相对路径, 文本)]——仓库根 *.html + js/*.js。"""
    root = repo_root or chains_mod.REPO_ROOT
    texts = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".html"):
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                texts.append((name, fh.read()))
    js_dir = os.path.join(root, "js")
    if os.path.isdir(js_dir):
        for name in sorted(os.listdir(js_dir)):
            if name.endswith(".js"):
                with open(os.path.join(js_dir, name), encoding="utf-8") as fh:
                    texts.append((f"js/{name}", fh.read()))
    return texts


def check_frontend_calls(chs=None, repo_root=None):
    """第三检: 前端调用点。链步骤端点 × 主站前端文本。

    返回 (call_map, no_call)——call_map: (method, path) → [命中文件];
    no_call: [{chain, step, label, method, path, kind='no_call'}]。
    internal/不在册端点跳过(后者第一检已报)。
    """
    chs = chs if chs is not None else chains_mod.parse_chains()
    texts = _frontend_texts(repo_root)
    reg = chains_mod.load_registry()
    known = {(e["method"], e["path"]) for e in reg["endpoints"]}
    seen, call_map, no_call = set(), {}, []
    for chain in chs:
        for step in chain["steps"]:
            for method, path, _amb in step["endpoints"]:
                if (method, path) in seen or step["internal"]:
                    continue
                seen.add((method, path))
                head, tail = _call_tokens(path)
                hits = []
                for fname, text in texts:
                    if head not in text:
                        continue
                    if tail and not (f"'{tail}'" in text or f'"{tail}"' in text):
                        continue
                    hits.append(fname)
                if hits:
                    call_map[(method, path)] = hits
                elif (method, path) in known:
                    no_call.append({
                        "chain": chain["name"], "step": step["no"],
                        "label": step["label"], "method": method, "path": path,
                        "kind": "no_call",
                    })
    return call_map, no_call


_FIELD_SPLIT_RE = re.compile(r"[/+、,，()（）\s]+")
_KEY_EXTRA = {"pay_no", "payout_no", "JWT", "level", "commission"}


def _field_tokens(fields):
    """衔接字段列 `入 → 出` → (入 tokens, 出 tokens); 无 → 返回空。"""
    if "→" not in fields:
        return [], []
    ins, _, outs = fields.partition("→")
    tok = lambda s: [t for t in _FIELD_SPLIT_RE.split(s.strip()) if t]
    return tok(ins), tok(outs)


def _is_key_field(t):
    """关键 identifier 抽验口径: *_id 后缀 + 少量关键产物名。"""
    return t.endswith("_id") or t in _KEY_EXTRA


def _linked(tok, outs):
    """宽匹配衔接: sku 命中 sku_id(简写/全称互含)。"""
    return any(tok in o or o in tok for o in outs)


def check_dataflow(chs=None):
    """第四检: 数据流衔接(从简——关键 ID 抽验)。

    规则: identifier 类入字段须在链内前序已出现的字段(入+出——链上下文延续,
    如 order_id 贯穿多步)中宽匹配命中; 未命中 → dataflow_gap(凭空出现的
    关键 ID, 外部输入或链定义缺字段)。链首步入字段豁免(外部输入起点)。
    产出未消费(如 JWT=链目标产物)不检——语义留给人工。
    返回 findings: [{chain, step, token, kind='dataflow_gap', prior_outs}]。
    """
    chs = chs if chs is not None else chains_mod.parse_chains()
    findings = []
    for chain in chs:
        produced = set()
        for i, step in enumerate(chain["steps"]):
            ins, outs = _field_tokens(step["fields"])
            if i > 0:
                for tok in ins:
                    if _is_key_field(tok) and not _linked(tok, produced):
                        findings.append({
                            "chain": chain["name"], "step": step["no"],
                            "label": step["label"], "token": tok,
                            "kind": "dataflow_gap",
                            "prior_outs": sorted(produced)[:6],
                        })
            produced.update(ins)
            produced.update(outs)
    return findings


def run(args=None):
    """audit CLI 入口: 输出 T1.1-T1.4 四检, T1.5 逐任务补齐。"""
    print("[zhuyi audit] L1 业务链审查 · T1.1 端点在册 / T1.2 鉴权可达 / "
          "T1.3 前端调用点 / T1.4 数据流衔接")
    findings, total = check_endpoint_registry()
    for f in findings:
        print(f"  [T1.1:{f['kind']}] {f['chain']} 步骤{f['step']}({f['label']}): "
              f"{f['method']} {f['path']}")
    print(f"  [T1.1] 扫描汇总: hit={total['hit']} partial={total['partial']} "
          f"miss={total['miss']} internal={total['internal']}")
    auth_findings = check_auth_reachable()
    if not auth_findings:
        print("  [T1.2 鉴权可达] 通过: 链上鉴权期望与 registry 实际面一致")
    for f in auth_findings:
        print(f"  [T1.2:{f['kind']}] {f['chain']} 步骤{f['step']}({f['label']}): "
              f"{f['method']} {f['path']} 期望{f['expect']} 实际{f['actual']}"
              + ("——401 永拦" if f["kind"] == "auth_401" else "——过度暴露"))
    call_map, no_call = check_frontend_calls()
    print(f"  [T1.3 前端调用点] called={len(call_map)} no_call={len(no_call)}")
    for (method, path), files in sorted(call_map.items()):
        print(f"  [T1.3:called] {method} {path} ← {', '.join(files[:3])}")
    for f in no_call:
        print(f"  [T1.3:{f['kind']}] {f['chain']} 步骤{f['step']}({f['label']}): "
              f"{f['method']} {f['path']}——主站前端无调用点(可能在 taro 端/服务间)")
    gaps = check_dataflow()
    if not gaps:
        print("  [T1.4 数据流衔接] 通过: 关键 ID 前序溯源全命中")
    for f in gaps:
        print(f"  [T1.4:{f['kind']}] {f['chain']} 步骤{f['step']}({f['label']}): "
              f"入字段「{f['token']}」未溯源至前序产出(外部输入或链定义缺字段, "
              f"前序产出: {', '.join(f['prior_outs']) or '无'})")
    print("[zhuyi audit] T1.5 断链报告: 后续任务实现")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
