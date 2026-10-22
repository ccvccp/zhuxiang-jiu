# dist 关键页面注入 ICP 备案页脚(鲁ICP备2026059492号 -> beian.miit.gov.cn)
# 用法: python inject_beian.py <dist_dir>
import os
import re
import sys

DIST = sys.argv[1] if len(sys.argv) > 1 else r"C:\www\dist"
PAGES = ["index.html", "official.html", "app.html", "store-map.html",
         "visual.html", "cart.html", "checkout.html", "products.html",
         "login.html"]

SNIPPET = (
    '<div id="zx-icp" style="display:block;clear:both;padding:14px 10px 16px;'
    'text-align:center;font-size:12px;color:#9aa;background:transparent;">'
    '<a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener" '
    'style="color:#9aa;text-decoration:none;">鲁ICP备2026059492号-3</a>'
    '&nbsp;|&nbsp;泰安市元禾生物科技有限公司</div></body>'
)

done, skipped = [], []
for name in PAGES:
    p = os.path.join(DIST, name)
    if not os.path.isfile(p):
        continue
    with open(p, encoding="utf-8", errors="replace") as f:
        body = f.read()
    if "鲁ICP备" in body or "zx-icp" in body:
        skipped.append(name)
        continue
    # 幂等: 只替换最后一个 </body>
    idx = body.rfind("</body>")
    if idx == -1:
        continue
    body = body[:idx] + SNIPPET + body[idx + len("</body>"):]
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    done.append(name)
print("injected:", done)
print("skipped(already):", skipped)
print("INJECT-BEIAN-DONE")
