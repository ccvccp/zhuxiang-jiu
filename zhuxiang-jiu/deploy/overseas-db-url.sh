#!/bin/bash
echo '=== DRAMA85_DB_URL in container ==='
docker exec zhuxiang-backend-1 sh -c 'env | grep DRAMA85'
echo '=== 容器内 backend/.env ==='
docker exec zhuxiang-backend-1 sh -c 'grep -h "DRAMA85_DB_URL" /app/backend/.env /app/.env 2>/dev/null | sed "s/:[^:@]*@/:***@/"'
