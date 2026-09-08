#!/bin/bash
# check-tls.sh · 验证 zxjiu.com TLS 协议版本支持(小程序要求 TLS>=1.2)
for v in tls1 tls1_1 tls1_2 tls1_3; do
  r=$(echo | openssl s_client -connect 127.0.0.1:443 -servername zxjiu.com -$v 2>/dev/null | grep "Protocol *:")
  echo "$v => ${r:-BLOCKED}"
done
