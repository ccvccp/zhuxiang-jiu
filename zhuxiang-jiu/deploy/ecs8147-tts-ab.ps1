$env:PYTHONUTF8 = '1'
cmd /c "C:\Python312\python.exe C:\tmp8147\tts_ab_v2.py > C:\tmp8147\tts_ab_run.log 2>&1"
Write-Host ('rc=' + $LASTEXITCODE)
Get-Content C:\tmp8147\tts_ab_run.log
Get-ChildItem C:\tmp8147\tts_ab_*.wav -ErrorAction SilentlyContinue | ForEach-Object { Write-Host ($_.Name + " " + $_.Length) }
Write-Host 'AB6-DONE'
