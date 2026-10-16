# 网络延迟尖峰修复: 禁用无线网卡"允许计算机关闭此设备以节约电源"
# 现状 PnPCapabilities=16(节电勾选) → 256(禁用节电), 需管理员运行
$key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\0001'
Set-ItemProperty -Path $key -Name PnPCapabilities -Value 256 -Type DWord
Write-Host ("PnPCapabilities -> " + (Get-ItemProperty $key).PnPCapabilities + " (256=已禁用节电)")
Restart-NetAdapter -Name WLAN
Write-Host "WLAN 已重启生效"
Start-Sleep -Seconds 8
ping 192.168.0.1 -n 30 -w 1500 | Select-Object -Last 5
Read-Host "按回车关闭"
