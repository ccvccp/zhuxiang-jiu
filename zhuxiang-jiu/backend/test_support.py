"""直跑测试自举模块(AUTH-TEST-01 P0)

背景:
    conftest.py 只在 pytest 启动时加载——项目测试惯例是直跑脚本
    (python test_xxx.py, __main__ 模式), 直跑不经过 conftest,
    导致三环境变量全部未设:
        ① AUTH_COMPAT_TRUST_HEADERS 未设 → 中间件剥离 X-Role
           → 路由层 _require_admin 403(HTTP 型测试全挂)
        ② STORE_MODE 落回 redis 默认 → 测试静默误连开发 Redis
        ③ LOCK_MODE 落回 redis 默认 → 并发锁走 Redis

用法(约定: 直跑测试首行环境导入):
    import test_support  # noqa: F401 (直跑自举: 内存模式+头信任)

    必须置于 repositories.backend / core.locks / main 导入之前,
    否则这两个模块在 import 时已读取到 redis 默认值。

安全边界:
    - setdefault 语义: 显式环境(redis 集成测试/CI)优先, 不覆盖
    - AUTH_COMPAT_TRUST_HEADERS=1 仅本测试进程内生效, 生产
      .env 从未设置(仅限 JWT 故障应急, 见 .env.example)
"""

import os

# 必须在 repositories.backend / core.locks 模块加载之前执行
os.environ.setdefault("LOCK_MODE", "asyncio")
os.environ.setdefault("STORE_MODE", "asyncio")

# compat 模式测试依赖旧头(X-Role: admin 直调模块管理端点);
# 生产默认剥离伪造身份头(见 core/auth_middleware.py)
os.environ.setdefault("AUTH_COMPAT_TRUST_HEADERS", "1")
