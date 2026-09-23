"""生产 .env: 注入短信五项凭据(幂等)

值不落 git(密钥红线)——从环境变量或本文件手工填入后执行:
    SMS_ALIYUN_ACCESS_KEY_ID=...       RAM子账号AK(zxjiu-sms)
    SMS_ALIYUN_ACCESS_KEY_SECRET=...
    SMS_ALIYUN_SIGN_NAME=泰安市元禾生物科技
    SMS_ALIYUN_TEMPLATE_CODE=SMS_338950350          验证码模板
    SMS_ALIYUN_MARGIN_TEMPLATE_CODE=SMS_512495225   保证金到期模板
真实值已于 2026-09-23 直接写入生产 /opt/zhuxiang/.env(实证通过 bizId=172110390180682291)。
"""
import io
import os

ENV_FILE = "/opt/zhuxiang/.env"
KEYS = (
    "SMS_ALIYUN_ACCESS_KEY_ID",
    "SMS_ALIYUN_ACCESS_KEY_SECRET",
    "SMS_ALIYUN_SIGN_NAME",
    "SMS_ALIYUN_TEMPLATE_CODE",
    "SMS_ALIYUN_MARGIN_TEMPLATE_CODE",
)


def main() -> None:
    s = io.open(ENV_FILE, encoding="utf-8").read()
    existing = {ln.split("=", 1)[0] for ln in
                s.split("\n") if "=" in ln
                and not ln.startswith("#")}
    add = [f"{k}={os.environ[k]}" for k in KEYS
           if k not in existing and os.environ.get(k)]
    if not add:
        print("nothing-to-add"
              "(全部已存在或环境变量未提供)")
        return
    body = s if s.endswith("\n") else s + "\n"
    body += "\n".join(add) + "\n"
    io.open(ENV_FILE, "w", encoding="utf-8") \
        .write(body)
    print(f"appended {len(add)} vars")


main()
