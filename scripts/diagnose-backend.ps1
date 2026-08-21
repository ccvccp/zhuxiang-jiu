# 综合诊断脚本: 一次性收集所有 backend 诊断信息
$ErrorActionPreference = "Continue"
cd d:\网站架构设计
$out = "d:\网站架构设计\backend-diagnose.txt"
"========== 1. 容器状态 ==========" | Out-File $out -Encoding utf8
docker compose ps -a 2>&1 | Out-File $out -Encoding utf8 -Append
"" | Out-File $out -Encoding utf8 -Append
"========== 2. Health 探针历史 ==========" | Out-File $out -Encoding utf8 -Append
$cid = docker compose ps -q backend 2>&1
"backend container id: $cid" | Out-File $out -Encoding utf8 -Append
if ($cid) {
    docker inspect $cid --format '{{json .State.Health}}' 2>&1 | Out-File $out -Encoding utf8 -Append
    "" | Out-File $out -Encoding utf8 -Append
    "========== 3. State 详情 ==========" | Out-File $out -Encoding utf8 -Append
    docker inspect $cid --format '{{json .State}}' 2>&1 | Out-File $out -Encoding utf8 -Append
}
"" | Out-File $out -Encoding utf8 -Append
"========== 4. Backend 完整日志 ==========" | Out-File $out -Encoding utf8 -Append
docker compose logs backend 2>&1 | Out-File $out -Encoding utf8 -Append
"" | Out-File $out -Encoding utf8 -Append
"========== 5. Redis 日志 ==========" | Out-File $out -Encoding utf8 -Append
docker compose logs --tail 20 redis 2>&1 | Out-File $out -Encoding utf8 -Append
"" | Out-File $out -Encoding utf8 -Append
"========== 6. /app 目录结构 ==========" | Out-File $out -Encoding utf8 -Append
docker compose exec backend ls -la /app/ 2>&1 | Out-File $out -Encoding utf8 -Append
"" | Out-File $out -Encoding utf8 -Append
"========== 7. 容器内进程 ==========" | Out-File $out -Encoding utf8 -Append
docker compose exec backend ps aux 2>&1 | Out-File $out -Encoding utf8 -Append
"" | Out-File $out -Encoding utf8 -Append
"========== 8. 容器内 curl health ==========" | Out-File $out -Encoding utf8 -Append
docker compose exec backend curl -v http://localhost:8000/api/decision/health 2>&1 | Out-File $out -Encoding utf8 -Append
"" | Out-File $out -Encoding utf8 -Append
"========== 9. import main 测试 ==========" | Out-File $out -Encoding utf8 -Append
docker compose exec backend python -c "import main; print('import OK')" 2>&1 | Out-File $out -Encoding utf8 -Append
"" | Out-File $out -Encoding utf8 -Append
"========== 10. Python path ==========" | Out-File $out -Encoding utf8 -Append
docker compose exec backend python -c "import sys; print(sys.path)" 2>&1 | Out-File $out -Encoding utf8 -Append
Write-Host "诊断完成, 结果已保存到 $out"
Get-Item $out | Select-Object Name, Length, LastWriteTime
