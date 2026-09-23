# Trae BOM Bug 反馈 48h 跟进提醒弹窗(计划任务调起)
Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show(
    "Trae CN「BOM 污染」Bug 反馈已提交满 48 小时。`n`n若无官方回复:`n  1. 走飞书客服/官方群二次跟进`n  2. 引用报告并强调: Data Corruption / Non-user Modification`n  3. 中文报告: zhuxiang-jiu\docs\Trae_CN_BOM污染Bug反馈.md`n  4. 英文报告: zhuxiang-jiu\docs\Trae_BOM_Pollution_Bug_Report_EN.md`n  5. 证据图:   zhuxiang-jiu\docs\BOM污染取证_证据总览图.png`n`n如已获回复, 忽略本提醒。",
    "Trae Bug 反馈跟进提醒 (48h)",
    0, 64)
