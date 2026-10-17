# Windows toast 通知 (无第三方依赖, WinRT 原生; 供 keepalive.js 调用)
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File toast_notify.ps1 -Title '...' -Body '...'
param([string]$Title = 'channels-bot', [string]$Body = '')
$ErrorActionPreference = 'silentlycontinue'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$xml.GetElementsByTagName('text')[0].AppendChild($xml.CreateTextNode($Title)) | Out-Null
$xml.GetElementsByTagName('text')[1].AppendChild($xml.CreateTextNode($Body)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
# AUMID 用 PowerShell 的已注册 AppId, 保证 Win10/11 能弹出
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($toast)
