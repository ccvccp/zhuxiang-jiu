---
id: C-20261021-02
title: nginx ssl_certificate 相对路径解析到 conf 目录致新证书从未加载
date: 2026-10-21
severity: P0
tech_stack: [nginx, deploy]
module: deploy
keywords: [nginx, 证书, ssl_certificate, 相对路径, conf目录, https, 时区, 指纹]
related_files:
  - deploy/ecs8147-certfix.ps1
  - deploy/ecs8147-certproof.ps1
related_chain: ''
related_commit: ''
checklist_refs: [NEW-E]
---

# 案例-20261021-nginx证书conf路径

## 一、现象

2026-10-21 公网访问 https://zyjiu.com 报腾讯"无法确认该网页的安全性，
请谨慎访问"。排查发现公网 443 返回的仍是旧单域名证书——新签发的双域名
证书部署后**从未被 nginx 加载**，连续 reload 无效，疑似"网络层截获"。

## 二、根因

nginx.conf 中 `ssl_certificate cert.pem;` 是**相对路径**，nginx 将其解析到
**conf 目录**（C:\nginx\conf\cert.pem），而非安装根目录。新证书写到了
根目录 C:\nginx\cert.pem，nginx 实际加载的 conf\cert.pem 仍是 10-11 旧证书。
定位过程中一度被**时区显示**误导：旧证书 notAfter "Jan 8 20:51 UTC"
在本地时区显示为"2027-01-09"，被误读为"新证书已生效"，浪费了 EIP 更换、
CDN 疑云、工单周旋等多轮排查。

## 三、错误代码/配置

服务器 C:\nginx\conf\nginx.conf（修复前语义）：

```nginx
ssl_certificate      cert.pem;    # 相对路径 → 解析到 C:\nginx\conf\cert.pem (旧证书)
ssl_certificate_key  key.pem;
```

新证书此前被部署到 C:\nginx\cert.pem（根目录）——从未被加载。

## 四、正确写法

把 Certbot live 目录的 fullchain/privkey **复制到 conf 目录**后 reload
（deploy/ecs8147-certfix.ps1）：C:\nginx\conf\cert.pem + conf\key.pem。
验证必须用 **X509 指纹（thumbprint）比对**而非 notAfter 字符串——
SslStream 双向握手（127.0.0.1 + 公网 IP，SNI=www）确认 thumb 一致
（0158ACA1），公网双域名新证书生效。

## 五、预防规则

证书类部署一律用**绝对路径**写 conf，验证一律**比指纹**（指纹才是身份，
notAfter 字符串会被时区显示欺骗）——挂 NEW-E（部署面规则，lead 评审
入竹盾检查清单 E 组）。
