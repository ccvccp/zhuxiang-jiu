# certbot V4 步骤2+3: 新签 v.zyjiu.com + SetCdnDomainSSLCertificate(ROA) 上传 + 续期自动化
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
function Step($m) { Write-Host ("==> " + $m) }
function Info($m) { Write-Host ("    " + $m) }

Step '[1/4] certbot 新签 v.zyjiu.com(挑战经 CDN 回源 OSS)'
$env:PYTHONUTF8 = '1'
cmd /c "cd /d C:\ && C:\Python312\Scripts\certbot.exe certonly --manual --preferred-challenges http -d v.zyjiu.com --manual-auth-hook C:\certbot-hooks\v-auth.bat --manual-cleanup-hook C:\certbot-hooks\v-cleanup.bat --email zhuxiang-ops@zyjiu.com --agree-tos --no-eff-email --non-interactive --keep-until-expiring > C:\tmp8147\certbot-v-sign.log 2>&1"
$rc = $LASTEXITCODE
Info ("certbot 退出码: " + $rc)
Get-Content C:\tmp8147\certbot-v-sign.log -Tail 10 | ForEach-Object { Info $_ }
if ($rc -ne 0) { exit 1 }

Step '[2/4] 落 CDN 证书上传器(ROA POST 签名)'
$cdndeploy = @'
import hmac, hashlib, base64, datetime, urllib.request, urllib.parse, sys, re, os

AK = os.environ.get('ALIYUN_AK_ID', '')
SK = os.environ.get('ALIYUN_AK_SECRET', '')
if not AK or not SK:
    raise SystemExit('set system env ALIYUN_AK_ID / ALIYUN_AK_SECRET (certbot renew hook needs them too)')

def roa_call(params):
    # CDN SetCdnDomainSSLCertificate 为 RPC 协议(POST 动词签名, 参数全在 query)
    p = dict(params)
    p['AccessKeyId'] = AK
    p['Format'] = 'JSON'
    p['Timestamp'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    p['SignatureMethod'] = 'HMAC-SHA1'
    p['SignatureVersion'] = '1.0'
    p['SignatureNonce'] = __import__('uuid').uuid4().hex
    def rpc_enc(s):
        return urllib.parse.quote(str(s), safe='').replace('+', '%20').replace('*', '%2A').replace('%7E', '~')
    qs = '&'.join(rpc_enc(k) + '=' + rpc_enc(v) for k, v in sorted(p.items()))
    sts = 'POST&' + rpc_enc('/') + '&' + rpc_enc(qs)
    sig = base64.b64encode(hmac.new((SK + '&').encode(), sts.encode(), hashlib.sha1).digest()).decode()
    url = 'https://cdn.aliyuncs.com/?' + qs + '&Signature=' + rpc_enc(sig)
    req = urllib.request.Request(url, data=b'', method='POST')
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        print('CDN API -> ' + str(resp.status) + ': ' + resp.read().decode('utf-8', 'replace')[:300])
        return 0
    except urllib.error.HTTPError as e:
        print('CDN API -> ' + str(e.code) + ': ' + e.read().decode('utf-8', 'replace')[:500])
        return 1

def deploy():
    lineage = sys.argv[1]          # C:\Certbot\live\v.zyjiu.com
    with open(lineage + r'\fullchain.pem', encoding='ascii') as f:
        pub = f.read()
    with open(lineage + r'\privkey.pem', encoding='ascii') as f:
        pri = f.read()
    name = 'le-' + datetime.datetime.now().strftime('%Y%m%d')
    return roa_call({
        'Action': 'SetCdnDomainSSLCertificate',
        'Version': '2018-05-10',
        'DomainName': 'v.zyjiu.com',
        'CertType': 'upload',
        'SSLProtocol': 'on',
        'SSLPub': pub,
        'SSLPri': pri,
        'CertName': name,
    })

if __name__ == '__main__':
    sys.exit(deploy())
'@
[IO.File]::WriteAllText('C:\certbot-hooks\cdndeploy.py', $cdndeploy.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
cmd /c "echo @C:\Python312\python.exe C:\certbot-hooks\cdndeploy.py C:\Certbot\live\v.zyjiu.com > C:\certbot-hooks\cdndeploy.bat"
Info 'cdndeploy 就位'

Step '[3/4] 上传新证书到 CDN'
cmd /c "C:\Python312\python.exe C:\certbot-hooks\cdndeploy.py C:\Certbot\live\v.zyjiu.com > C:\tmp8147\cdn-deploy.log 2>&1"
$depRc = $LASTEXITCODE
Get-Content C:\tmp8147\cdn-deploy.log | ForEach-Object { Info $_ }
if ($depRc -ne 0) { exit 1 }

Step '[4/4] 续期自动化(v lineage deploy_hook) + 生效验证'
$rc2 = 'C:\Certbot\renewal\v.zyjiu.com.conf'
$lines = [IO.File]::ReadAllLines($rc2, [Text.Encoding]::UTF8)
if (-not ($lines | Where-Object { $_ -match '^deploy_hook' })) {
    $lines + 'deploy_hook = C:\certbot-hooks\cdndeploy.bat' | Set-Content $rc2 -Encoding ascii
    Info 'v.zyjiu deploy_hook 已追加'
} else { Info 'deploy_hook 已存在' }
Info 'CDN 证书部署生效约 1-5 分钟, 稍后由本地验证 TLS'
Write-Host 'V4-STEP23-DONE'
