"""容器内: 抓取政府网政策全文提取正文 → /tmp/policy_text.txt + 预览"""
import re
import urllib.request

URL = ("https://www.gov.cn/zhengce/zhengceku/2021-12/07/"
       "content_5658570.htm")
UA = "ZhuxiangKnowledgeBot/1.0 (+https://zxjiu.com)"

req = urllib.request.Request(URL, headers={"User-Agent": UA})
with urllib.request.urlopen(req, timeout=20) as r:
    html = r.read().decode("utf-8", "ignore")
print("HTML 字符:", len(html))

m = re.search(
    r'(?:id="UCAP-CONTENT"|class="pages_content")[^>]*>(.*?)</div>',
    html, re.S)
body = m.group(1) if m else html
text = re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.S)
text = re.sub(r"<[^>]+>", "\n", text)
text = re.sub(r"&nbsp;?", " ", text)
text = re.sub(r"[ \t]+", " ", text)
lines = [ln.strip() for ln in text.splitlines()]
lines = [ln for ln in lines if ln]
full = "\n".join(lines)
with open("/tmp/policy_text.txt", "w", encoding="utf-8") as f:
    f.write(full)
print("正文行数:", len(lines), "| 字符:", len(full))
print("竹食品:", full.count("竹食品"), "| 竹医药:", full.count("竹医药"),
      "| 竹笋:", full.count("竹笋"))
print("== 前12行 ==")
for ln in lines[:12]:
    print(" ", ln[:60])
print("== 后6行 ==")
for ln in lines[-6:]:
    print(" ", ln[:60])
