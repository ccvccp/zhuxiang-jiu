# 生产接线验证 v2: 退出码判定 + 结果落文件回读
$ErrorActionPreference = 'Continue'
$exe = 'C:\Python312\python.exe'
$code = @'
import sys, inspect, json
sys.path.insert(0, r'C:\backend')
from services.drama76_series_service import DramaSeriesService
from services import drama84_script_service as d84
sig = str(inspect.signature(DramaSeriesService.release_episode))
lines = ['SIG-76 ' + sig,
         'SHORT ' + repr(d84.SHORT_DURATION),
         'WIRE ' + json.dumps({
             'free_field': 'free_episodes' in sig,
             'micro_band': d84.SHORT_DURATION == (30, 90)})]
open(r'C:\tmp8147\probe_result.txt', 'w', encoding='utf-8').write('\n'.join(lines))
ok = ('free_episodes' in sig) and (d84.SHORT_DURATION == (30, 90))
sys.exit(0 if ok else 3)
'@
& $exe -c $code 2> C:\tmp8147\probe_err.txt
Write-Host ("PY-EXIT: $LASTEXITCODE")
if (Test-Path C:\tmp8147\probe_result.txt) {
    Write-Host '--- probe_result.txt ---'
    Get-Content C:\tmp8147\probe_result.txt | ForEach-Object { Write-Host $_ }
}
if (Test-Path C:\tmp8147\probe_err.txt) {
    $err = Get-Content C:\tmp8147\probe_err.txt -Raw
    if ($err -and $err.Trim()) { Write-Host '--- stderr ---'; Write-Host $err }
}
Write-Host 'PROBE2-DONE'
