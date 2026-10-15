"""外部依赖凭证就绪自检(P2 批次②③五域 + payment 既有渠道)

用途:
    凭证就绪前/后一键核对各域灰度态与凭证配置状态,
    输出切换建议——不打印任何凭证值(只显示 已配置/缺失)。

运行:
    python check_external_creds.py            # 文本报告
    python check_external_creds.py --json     # JSON 输出(供部署流水线)

退出码:
    0 = 自检完成(与凭证是否就绪无关; 就绪度看报告)
    1 = 参数错误

覆盖域(Provider 三态范式, 详见《docs/外部凭证接入Runbook.md》):
    高德 LBS / 花呗白条(payment) / 电子面单理赔(物流) /
    NFC 防伪 / 区块链存证 / 微信支付宝基础渠道(payment 既有)
"""
import json
import os
import sys

# 域定义: (域名, mode 开关, 必需凭证, 可选凭证, 回调令牌, real 冒烟提示)
DOMAINS = [
    {
        "domain": "高德地图 LBS",
        "module": "location",
        "modeEnv": "LOC_AMAP_MODE",
        "required": ["AMAP_KEY"],
        "optional": ["AMAP_SECRET"],
        "callbackToken": None,
        "smoke": "POST /api/location/address/add(不带坐标) → geocodeSource=amap",
    },
    {
        "domain": "微信支付 V3(payment 既有)",
        "module": "payment",
        "modeEnv": "PAY60_CHANNEL_MODE",
        "required": ["PAY60_CHANNEL_KEY",
                     "PAY60_WECHAT_MCHID", "PAY60_WECHAT_APPID",
                     "PAY60_WECHAT_SERIAL_NO",
                     "PAY60_WECHAT_PRIVATE_KEY"],
        "optional": ["PAY60_NOTIFY_URL"],
        "callbackToken": None,
        "smoke": "POST /api/payment/pay(wechat) → payParams 含五元组/h5 链接",
    },
    {
        "domain": "支付宝/花呗(payment 既有+批次②)",
        "module": "payment",
        "modeEnv": "PAY60_CHANNEL_MODE",
        "required": ["PAY60_CHANNEL_KEY",
                     "PAY60_ALIPAY_APPID", "PAY60_ALIPAY_PRIVATE_KEY"],
        "optional": ["PAY60_NOTIFY_URL"],
        "callbackToken": None,
        "smoke": "POST /api/payment/pay(huabei, installments=6) → payUrl"
                 " 含 enable_pay_installment",
    },
    {
        "domain": "京东白条(批次②③)",
        "module": "payment",
        "modeEnv": "PAY60_CHANNEL_MODE",
        "required": ["PAY60_CHANNEL_KEY",
                     "PAY60_BAITIAO_MERCHANT", "PAY60_BAITIAO_KEY"],
        "optional": [],
        "callbackToken": None,
        "smoke": "POST /api/payment/pay(baitiao, installments=3) → payUrl",
    },
    {
        "domain": "物流电子面单/理赔(批次③)",
        "module": "logistics",
        "modeEnv": "LGS_CARRIER_MODE",
        "required": ["LGS_KDN_EBUSINESS_ID", "LGS_KDN_APP_KEY"],
        "optional": [],
        "callbackToken": ("LGS_CALLBACK_TOKEN",
                          "real/mock_fallback 回调强制验签, 缺失即 401"),
        "smoke": "POST /api/logistics/order(useEwaybill=true) → 真实运单号"
                 "(注意: real 出单即计费, 仅生产验证)",
    },
    {
        "domain": "NFC 防伪(批次③)",
        "module": "trace",
        "modeEnv": "TRACE_ANTI_MODE",
        "required": ["TRACE_NFC_VENDOR_URL", "TRACE_NFC_API_KEY"],
        "optional": [],
        "callbackToken": None,
        "smoke": "POST /api/trace/nfc/bind(admin, 无 nfcUid) → 厂商 UID",
    },
    {
        "domain": "区块链存证(批次③)",
        "module": "location",
        "modeEnv": "CHAIN_EVIDENCE_MODE",
        "required": ["CHAIN_NODE_URL", "CHAIN_AK", "CHAIN_SECRET"],
        "optional": ["CHAIN_CONTRACT"],
        "callbackToken": None,
        "smoke": "POST /api/location/blockchain/evidence → chainVendor=chain"
                 " + chainStatus 推进 onchain",
    },
    {
        "domain": "短剧研判引擎数据源(85号)",
        "module": "drama85",
        "modeEnv": "DRAMA85_SOURCE_MODE",
        "required": ["DRAMA85_SOURCE_URL", "DRAMA85_SOURCE_API_KEY"],
        "optional": ["DRAMA85_SOURCE_SECRET", "DRAMA85_SOURCE_QPS"],
        "callbackToken": None,
        "smoke": "POST /api/drama85/collect(admin) → 报告"
                 " sourceChannel=real(契约见 drama85_source_gateway)",
    },
]

VALID_MODES = ("mock", "mock_fallback", "real")


def _configured(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def inspect() -> list[dict]:
    """逐域检查(只读 env, 不发起任何外部调用)"""
    report = []
    for d in DOMAINS:
        mode = ((os.environ.get(d["modeEnv"]) or "mock")
                .strip().lower())
        mode = mode if mode in VALID_MODES else "mock(非法值回退)"
        req_missing = [k for k in d["required"] if not _configured(k)]
        opt_missing = [k for k in d["optional"] if not _configured(k)]
        token_name, token_note = d["callbackToken"] or (None, "")
        token_missing = (token_name and not _configured(token_name))
        # 就绪度
        if not req_missing and not token_missing:
            readiness = "ready"
        elif req_missing:
            readiness = "missing_credentials"
        else:
            readiness = "missing_callback_token"
        # 切换建议
        if mode.startswith("mock") and mode == "mock":
            advice = ("当前 mock; 凭证就绪后先 mock_fallback 灰度"
                      "(real 失败自动降级留痕), 稳定后切 real")
        elif mode == "mock_fallback":
            advice = "灰度中; 观察 fallback 留痕, 连续无降级后切 real"
        else:
            advice = "real 已启用; 凭证变更须走回滚 → mock_fallback → real"
        report.append({
            "domain": d["domain"],
            "module": d["module"],
            "modeEnv": d["modeEnv"],
            "mode": mode,
            "required": [{"name": k,
                          "configured": _configured(k)}
                         for k in d["required"]],
            "optional": [{"name": k,
                          "configured": _configured(k)}
                         for k in d["optional"]],
            "callbackToken": ({"name": token_name,
                               "configured": not token_missing,
                               "note": token_note}
                              if token_name else None),
            "readiness": readiness,
            "advice": advice,
            "smoke": d["smoke"],
        })
    return report


def _fmt(report: list[dict]) -> str:
    lines = ["外部依赖凭证就绪自检", "=" * 62]
    for r in report:
        lines.append(f"\n[{r['domain']}] ({r['module']} · "
                     f"{r['modeEnv']}={r['mode']})")
        for item in r["required"]:
            mark = "OK " if item["configured"] else "缺失"
            lines.append(f"  [{mark}] {item['name']}"
                         + ("" if item["configured"] else "  (必需)"))
        for item in r["optional"]:
            mark = "OK " if item["configured"] else "-- "
            lines.append(f"  [{mark}] {item['name']} (可选)")
        if r["callbackToken"]:
            mark = "OK " if r["callbackToken"]["configured"] else "缺失"
            lines.append(f"  [{mark}] {r['callbackToken']['name']} "
                         f"({r['callbackToken']['note']})")
        lines.append(f"  就绪度: {r['readiness']}")
        lines.append(f"  建议: {r['advice']}")
        lines.append(f"  real 冒烟: {r['smoke']}")
    ready = [r for r in report if r["readiness"] == "ready"]
    lines.append("\n" + "=" * 62)
    lines.append(f"汇总: {len(ready)}/{len(report)} 域凭证就绪"
                 f"(其余维持 mock, 零凭证本地/CI 全绿不受影响)")
    return "\n".join(lines)


def main() -> int:
    if "--json" in sys.argv[1:]:
        print(json.dumps(inspect(), ensure_ascii=False, indent=2))
    else:
        print(_fmt(inspect()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
