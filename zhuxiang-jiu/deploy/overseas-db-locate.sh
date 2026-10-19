#!/bin/bash
echo '=== docker 容器 ==='
docker ps --format '{{.Names}} | {{.Image}} | {{.Ports}}'
echo '=== 3306 监听 ==='
ss -tlnp | grep 3306 || echo 'no 3306'
echo '=== backend 进程/uvicorn ==='
ps aux | grep -iE 'uvicorn|fastapi|python.*main' | grep -v grep | head -5
