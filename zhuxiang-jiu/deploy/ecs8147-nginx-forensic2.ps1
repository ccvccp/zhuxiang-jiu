# 取证2: nginx.conf 全文 + include 的子配置 + html 响应头重试
$ErrorActionPreference = 'Continue'
Write-Host '== C:\nginx\conf\nginx.conf =='
Get-Content C:\nginx\conf\nginx.conf
Write-Host ''
Write-Host '== conf 目录其他文件 =='
Get-ChildItem C:\nginx\conf -File | Select-Object Name, Length | Format-Table -AutoSize | Out-String -Width 120
foreach ($f in @(Get-ChildItem C:\nginx\conf -Filter '*.conf' -File | Where-Object Name -ne 'nginx.conf')) {
    Write-Host ('== ' + $f.FullName + ' ==')
    Get-Content $f.FullName
}
Write-Host ''
Write-Host '== html 响应头(GET 方式) =='
curl.exe -s -D - -o NUL -m 8 -H 'Host: zxjiu.com' http://127.0.0.1/official.html
