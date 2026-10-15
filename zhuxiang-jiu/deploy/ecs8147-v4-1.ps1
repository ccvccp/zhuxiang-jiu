# certbot V4 步骤1: OSS V4 签名 hook(python) + 挑战链路自检
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host ("==> " + $m) }
function Info($m) { Write-Host ("    " + $m) }

Step '[1/3] 落 OSS V4 签名库 + hooks'
$osslib = @'
import hmac, hashlib, datetime, sys, os, urllib.request, urllib.parse

AK = os.environ.get('ALIYUN_AK_ID', '')
SK = os.environ.get('ALIYUN_AK_SECRET', '')
if not AK or not SK:
    raise SystemExit('set system env ALIYUN_AK_ID / ALIYUN_AK_SECRET (certbot renew hook needs them too)')
BUCKET = 'zxjiu-videos'
REGION = 'cn-hangzhou'
HOST = BUCKET + '.oss-cn-hangzhou.aliyuncs.com'

def v4_request(method, key, body=b'', ctype='text/plain'):
    now = datetime.datetime.now(datetime.timezone.utc)
    datestamp = now.strftime('%Y%m%d')
    amzdate = now.strftime('%Y%m%dT%H%M%SZ')
    payload_hash = 'UNSIGNED-PAYLOAD'   # 阿里云 OSS V4 仅支持 UNSIGNED-PAYLOAD
    canonical_uri = '/' + '/'.join(urllib.parse.quote(s, safe='') for s in key.split('/'))
    # 签名用 CanonicalURI 含 bucket 前缀(虚拟主机 URL 不带)
    sign_uri = '/' + BUCKET + canonical_uri
    headers = {'x-oss-content-sha256': payload_hash, 'x-oss-date': amzdate}   # host 不参与 OSS V4 签名
    if ctype:
        headers['content-type'] = ctype
    names = sorted(headers)
    canonical_headers = ''.join(n + ':' + headers[n] + '\n' for n in names)
    cr = method + '\n' + sign_uri + '\n' + '' + '\n' + canonical_headers + '\n' + '' + '\n' + payload_hash
    scope = datestamp + '/' + REGION + '/oss/aliyun_v4_request'
    sts = 'OSS4-HMAC-SHA256\n' + amzdate + '\n' + scope + '\n' + hashlib.sha256(cr.encode()).hexdigest()
    def h(k, m): return hmac.new(k, m.encode(), hashlib.sha256).digest()
    dk = h(('aliyun_v4' + SK).encode(), datestamp)
    dk = h(dk, REGION); dk = h(dk, 'oss'); dk = h(dk, 'aliyun_v4_request')
    sig = hmac.new(dk, sts.encode(), hashlib.sha256).hexdigest()
    headers['authorization'] = 'OSS4-HMAC-SHA256 Credential=' + AK + '/' + scope + ',Signature=' + sig
    url = 'https://' + HOST + canonical_uri
    req = urllib.request.Request(url, data=body if method == 'PUT' else None, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        sys.stderr.write('OSS ' + method + ' ' + key + ' -> ' + str(resp.status) + '\n')
        resp.read()
        return 0
    except urllib.error.HTTPError as e:
        sys.stderr.write('OSS ' + method + ' ' + key + ' -> ' + str(e.code) + ': ' + e.read().decode('utf-8', 'replace')[:1500] + '\n')
        return 1

if __name__ == '__main__':
    import os
    action = sys.argv[1] if len(sys.argv) > 1 else 'put'
    key = '.well-known/acme-challenge/' + os.environ.get('CERTBOT_TOKEN', 'selftest-v4')
    if action == 'put':
        val = os.environ.get('CERTBOT_VALIDATION', 'selftest-v4-value').encode()
        sys.exit(v4_request('PUT', key, body=val))
    elif action == 'del':
        sys.exit(v4_request('DELETE', key, body=b'', ctype=''))
'@
[IO.File]::WriteAllText('C:\certbot-hooks\oss4lib.py', $ossLib.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
cmd /c "echo @C:\Python312\python.exe C:\certbot-hooks\oss4lib.py put > C:\certbot-hooks\v-auth.bat"
cmd /c "echo @C:\Python312\python.exe C:\certbot-hooks\oss4lib.py del > C:\certbot-hooks\v-cleanup.bat"
Info 'oss4lib.py + v-auth/v-cleanup.bat 就位'

Step '[2/3] V4 签名自检: PUT -> 公共读 GET(OSS+CDN 双路径) -> DELETE'
$env:PYTHONUTF8 = '1'
cmd /c "C:\Python312\python.exe C:\certbot-hooks\oss4lib.py put > C:\tmp8147\oss4-selftest.log 2>&1"
$rc1 = $LASTEXITCODE
Get-Content C:\tmp8147\oss4-selftest.log | ForEach-Object { Info $_ }
Info ("PUT 退出码: " + $rc1)
if ($rc1 -ne 0) { exit 1 }

$g1 = curl.exe -s -m 15 -o NUL -w '%{http_code}' https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/.well-known/acme-challenge/selftest-v4
Info ("OSS 直读: " + $g1)
$v1 = curl.exe -s -m 15 https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/.well-known/acme-challenge/selftest-v4
Info ("OSS 内容: " + $v1)
$g2 = curl.exe -s -m 20 -o NUL -w '%{http_code}' https://v.zyjiu.com/.well-known/acme-challenge/selftest-v4
Info ("CDN 域名读: " + $g2 + " (200=LE 挑战链路全通; 404=CDN 缓存/回源问题)")
$v2 = curl.exe -s -m 20 https://v.zyjiu.com/.well-known/acme-challenge/selftest-v4
Info ("CDN 内容: " + $v2)

cmd /c "C:\Python312\python.exe C:\certbot-hooks\oss4lib.py del > C:\tmp8147\oss4-del.log 2>&1"
Get-Content C:\tmp8147\oss4-del.log | ForEach-Object { Info $_ }
Info ("DEL 退出码: " + $LASTEXITCODE)

if ($g1 -ne '200' -or $v1 -ne 'selftest-v4-value') { Info '!! OSS PUT/GET 失败'; exit 1 }
if ($g2 -ne '200') { Info '!! CDN 路径不通(LE 验证会失败)'; exit 1 }
Info 'V4 挑战链路全通'

Step '[3/3] 完成'
Write-Host 'V4-STEP1-DONE'
