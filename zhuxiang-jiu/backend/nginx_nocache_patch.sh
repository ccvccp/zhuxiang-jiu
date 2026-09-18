#!/bin/bash
# nginx 缓存例外: 补丁 chunk(app/taro) no-cache, 其余 /js/ 保持 30d immutable
set -e
CONF=$(ls /etc/nginx/sites-enabled/zxjiu.conf 2>/dev/null || echo /etc/nginx/conf.d/zxjiu.conf)
echo "conf: $CONF"
cp "$CONF" "${CONF}.bak-auth401"

python3 - <<'PYEOF'
conf = None
import glob
for p in ("/etc/nginx/sites-enabled/zxjiu.conf",
          "/etc/nginx/conf.d/zxjiu.conf"):
    try:
        src = open(p, encoding="utf-8").read()
        if "location /js/" in src:
            conf = p
            break
    except OSError:
        continue
assert conf, "nginx conf not found"
src = open(conf, encoding="utf-8").read()
old = '    location /js/  { expires 30d; add_header Cache-Control "public, immutable"; }'
new = (
    '    location = /js/app.eebb43ae.js { expires -1; add_header Cache-Control "no-cache"; }\n'
    '    location = /js/taro.1560daa0.js { expires -1; add_header Cache-Control "no-cache"; }\n'
    '    location /js/  { expires 30d; add_header Cache-Control "public, immutable"; }'
)
assert src.count(old) == 1, f"anchor count={src.count(old)}"
open(conf, "w", encoding="utf-8").write(src.replace(old, new))
print("[OK] nginx conf patched")
PYEOF

nginx -t && systemctl reload nginx && echo "[OK] nginx reloaded"
