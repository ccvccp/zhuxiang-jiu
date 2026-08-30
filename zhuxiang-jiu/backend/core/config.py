"""应用配置:CORS / API_BASE / 运行模式 / 角色等级 / 日志"""

import logging
import os
from datetime import datetime, UTC

# CORS 白名单(开发环境允许 localhost + file:// 直开 null origin,
# 生产环境必须通过环境变量 CORS_ORIGINS 收紧为具体域名)
CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:8080,http://localhost:3000,"
    "http://127.0.0.1:8080,http://127.0.0.1:3000,null",
).split(",")

API_BASE = "/api/decision"
START_TIME = datetime.now(UTC)
APP_MODE = {"mode": "mock", "api_base": API_BASE}

# 角色等级(数字越大权限越高)
ROLE_LEVELS = {
    "guest": 0, "member": 1, "agent": 2,
    "store_owner": 3, "admin": 4,
}

ALLOW_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
ALLOW_HEADERS = ["Authorization", "Content-Type", "X-Role", "X-Member-Id",
                 "X-Agent-Id", "X-Admin-Id"]


# ============================================================
#  日志配置
# ============================================================
# 通过 LOG_LEVEL 环境变量控制级别(默认 INFO, 排查并发问题可设 DEBUG)
# 测试时由 pytest.ini 的 log_cli 接管输出, basicConfig 幂等不冲突
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
