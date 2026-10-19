#!/bin/bash
echo '=== 实际 DB_URL ==='
docker exec zhuxiang-backend-1 python -c "import db; print(db.DB_URL)" 2>&1 | tail -1
