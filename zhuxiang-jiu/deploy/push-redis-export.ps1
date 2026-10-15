# credentials injected from local env (never hardcode): set ALIYUN_AK_ID / ALIYUN_AK_SECRET before running
if (-not $env:ALIYUN_AK_ID -or -not $env:ALIYUN_AK_SECRET) { throw 'set $env:ALIYUN_AK_ID / $env:ALIYUN_AK_SECRET first' }
$bash = @'
AKID=__ALIYUN_AK_ID__
SECRET=__ALIYUN_AK_SECRET__
DATE=$(date -u "+%a, %d %b %Y %H:%M:%S GMT")
OBJ="/zxjiu-videos/deploy/redis-logical/redis_export.jsonl.gz"
SIG=$(printf "PUT\n\napplication/gzip\n%s\n%s" "$DATE" "$OBJ" | openssl dgst -sha1 -hmac "$SECRET" -binary | base64)
curl -s -o /dev/null -w "PUT %{http_code}\n" -X PUT -H "Date: $DATE" -H "Content-Type: application/gzip" -H "Authorization: OSS ${AKID}:${SIG}" --data-binary @/tmp/redis_export.jsonl.gz "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/redis-logical/redis_export.jsonl.gz"
curl -s -o /dev/null -w "HEAD %{http_code} size=%{size_download}\n" -I "https://zxjiu-videos.oss-cn-hangzhou.aliyuncs.com/deploy/redis-logical/redis_export.jsonl.gz"
'@
$bash = $bash.Replace('__ALIYUN_AK_ID__', $env:ALIYUN_AK_ID).Replace('__ALIYUN_AK_SECRET__', $env:ALIYUN_AK_SECRET)
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bash))
ssh -o ConnectTimeout=20 root@47.236.61.117 "echo $b64 | base64 -d | bash"
