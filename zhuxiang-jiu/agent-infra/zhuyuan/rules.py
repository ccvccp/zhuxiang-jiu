"""zhuyuan rules: 合规词表引擎（80号·T1.1, 微信外链整改 20 处实战沉淀）

违禁词 regex 库, 对齐 BW-AIH compliance_rules schema:
rule_id / category / risk_level / pattern / legal_reference。
四大类: 功效宣称/怂恿饮酒/无依据营养声称/绝对化用语(+医疗暗示附类)。
match() 供 comply(T1.2) 消费; 注意: 「未成年人禁止饮酒」等**合规警示语**
不在词表内——警示语是整改后的正确写法, 不得误拦。
"""
import re

RULES = [
    {
        "rule_id": "ALC-FUNC",
        "category": "功效宣称",
        "risk_level": "high",
        "pattern": r"醒酒|解酒|不头疼|无酒气|清心怡神|懂你的身体|壮阳|消除紧张",
        "legal_reference": "《广告法》第22条: 酒类广告不得明示或暗示消除紧张、焦虑、增加体力等功效",
    },
    {
        "rule_id": "ALC-INCITE",
        "category": "怂恿饮酒",
        "risk_level": "high",
        "pattern": r"就喝竹奕酒|畅饮|不醉不归|尽兴更尽兴",
        "legal_reference": "《广告法》第22条: 不得诱导、怂恿饮酒或宣传无节制饮酒",
    },
    {
        "rule_id": "ALC-HEALTH",
        "category": "无依据营养声称",
        "risk_level": "high",
        "pattern": r"富硒|天然健康|有益成分|有益微量元素|健康密码",
        "legal_reference": "《广告法》第28条: 广告以虚假或引人误解内容欺骗消费者(无检测依据的营养声称)",
    },
    {
        "rule_id": "AD-ABSOLUTE",
        "category": "绝对化用语",
        "risk_level": "med",
        "pattern": r"最好|最佳|顶级|最高级|国家级|驰名商标|百分百有效",
        "legal_reference": "《广告法》第9条: 禁止使用'国家级''最高级''最佳'等用语"
                           "(注: '第一'因日常搭配误报率高未入 regex, 留人工审)",
    },
    {
        "rule_id": "AD-MED",
        "category": "医疗暗示",
        "risk_level": "high",
        "pattern": r"治疗|治胃病|疗效|防癌|药用",
        "legal_reference": "《广告法》第17条: 除医疗/药品/医疗器械广告外, 不得涉及疾病治疗功能",
    },
]

# 整改后的合规写法参照(供 T3.1 处方引用, 也是 match 零误报的口径样本)
SAFE_EXAMPLES = ["竹香清雅", "入口绵柔", "回味悠长", "晶莹剔透", "醇厚丰满"]

_COMPILED = [(r["rule_id"], r["category"], r["risk_level"],
              re.compile(r["pattern"])) for r in RULES]


def match(text):
    """文本 → 违规命中 [{rule_id, category, risk, word}]。

    合规警示语(未成年人禁止饮酒/过量饮酒有害健康)天然不在词表, 零误报。
    """
    hits = []
    for rule_id, category, risk, rx in _COMPILED:
        for m in rx.finditer(text):
            hits.append({"rule_id": rule_id, "category": category,
                         "risk": risk, "word": m.group(0)})
    return hits


def run(args=None):
    """rules 自检: 分类统计+规则数(词表健康度)。"""
    print(f"[zhuyuan rules] 违禁词规则 {len(RULES)} 组:")
    for r in RULES:
        print(f"  [{r['risk_level']}] {r['rule_id']} {r['category']}: {r['pattern']}")
    print(f"  合规写法参照: {', '.join(SAFE_EXAMPLES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
