"""应用配置:CORS / API_BASE / 运行模式 / 角色等级"""

import os
from datetime import datetime, timezone

# CORS 白名单(开发环境允许 localhost,生产环境通过环境变量配置)
CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:8080,http://localhost:3000,http://127.0.0.1:8080,http://127.0.0.1:3000",
).split(",")

API_BASE = "/api/decision"
START_TIME = datetime.now(timezone.utc)
APP_MODE = {"mode": "mock", "api_base": API_BASE}

# 角色等级(数字越大权限越高)
ROLE_LEVELS = {
    "guest": 0, "member": 1, "agent": 2,
    "store_owner": 3, "admin": 4,
}

ALLOW_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
ALLOW_HEADERS = ["Authorization", "Content-Type", "X-Role"]
