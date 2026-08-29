"""酒类合规年龄门(设计文档 P0-1: 未成年禁售)

规则:
    - 法定饮酒年龄: 18 周岁
    - 成年判定依据出生日期(YYYY-MM-DD), 按周岁计算
    - 三个入口: 注册(birthdate 硬校验) / 下单(声明强制+硬拦截) / 聊天(声明式记录)
"""

from datetime import date

# 法定饮酒年龄(周岁)
MIN_AGE = 18

# 未成年硬拦截文案(统一定义, 避免 wording 漂移)
MINOR_REJECT_MSG = "未成年人禁止购买酒类商品(法定饮酒年龄18周岁)"

# 下单首次成年声明缺失文案
AGE_CONFIRM_REQUIRED_MSG = (
    "酒类商品仅向成年人销售, 下单须确认已满18周岁(ageConfirmed=true)"
)


def parse_birthdate(birthdate: str) -> date:
    """解析出生日期字符串(YYYY-MM-DD)

    Raises:
        ValueError: 格式非法或非有效日期
    """
    if not birthdate or not isinstance(birthdate, str):
        raise ValueError("出生日期格式非法(须为 YYYY-MM-DD)")
    try:
        return date.fromisoformat(birthdate)
    except ValueError:
        raise ValueError("出生日期格式非法(须为 YYYY-MM-DD)") from None


def calc_age(birthdate: str, today: date = None) -> int:
    """按出生日期计算周岁年龄"""
    birth = parse_birthdate(birthdate)
    today = today or date.today()
    age = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        age -= 1
    return age


def is_adult(birthdate: str) -> bool:
    """是否达到法定饮酒年龄(>= 18 周岁)"""
    return calc_age(birthdate) >= MIN_AGE
