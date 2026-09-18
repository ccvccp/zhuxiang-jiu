#!/bin/bash
# 401 自愈补丁最终部署: chunk 改名(绕过 immutable 缓存) + index.html 引用更新
set -e
cd /var/www/zxjiu/dist

# 1. 备份 index.html
cp index.html index.html.bak-auth401

# 2. 以打完补丁的文件为基础生成新名 chunk(保留原名字文件兜底老缓存)
cp js/app.eebb43ae.js js/app.a401fix.js
cp js/taro.1560daa0.js js/taro.a401fix.js

# 3. index.html 引用更新(幂等: 仅在引用旧名时替换)
python3 - <<'PYEOF'
p = "/var/www/zxjiu/dist/index.html"
src = open(p, encoding="utf-8").read()
assert src.count("/js/taro.1560daa0.js") == 1, "taro 引用计数异常"
assert src.count("/js/app.eebb43ae.js") == 1, "app 引用计数异常"
src = src.replace("/js/taro.1560daa0.js", "/js/taro.a401fix.js")
src = src.replace("/js/app.eebb43ae.js", "/js/app.a401fix.js")
open(p, "w", encoding="utf-8").write(src)
print("[OK] index.html 引用已更新为新 chunk 名")
PYEOF

# 4. 恢复 nginx 配置(不再需要 no-cache 例外, 新名字无缓存历史)
CONF=/etc/nginx/conf.d/zxjiu.conf
if [ -f "${CONF}.bak-auth401" ]; then
    cp "${CONF}.bak-auth401" "$CONF"
    nginx -t && systemctl reload nginx && echo "[OK] nginx 配置已还原并重载"
fi

# 5. 验证
echo "--- 验证 ---"
grep -o 'src="/js/[^"]*"' index.html
curl -sk -o /dev/null -w 'app.a401fix.js: %{http_code}\n' \
    https://127.0.0.1/js/app.a401fix.js --resolve zxjiu.com:443:127.0.0.1
curl -sk -o /dev/null -w 'taro.a401fix.js: %{http_code}\n' \
    https://127.0.0.1/js/taro.a401fix.js --resolve zxjiu.com:443:127.0.0.1
curl -sk -o /dev/null -w 'index.html: %{http_code}\n' \
    https://127.0.0.1/ --resolve zxjiu.com:443:127.0.0.1
curl -s https://127.0.0.1/ --resolve zxjiu.com:443:127.0.0.1 -k \
    | grep -o 'src="/js/[^"]*"'
echo "[OK] 部署完成: 新名 chunk 生效, 老名文件保留兜底"
