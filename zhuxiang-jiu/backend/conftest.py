"""pytest 全局配置:强制内存模式,保护现有测试不依赖 Redis

设计:
    - 测试通过 `from main import _mock_store` 直接修改内存字典
    - Phase 2 起, LOCK_MODE/STORE_MODE 默认值为 redis(生产优先)
    - 此处在 pytest 启动前强制覆盖为 asyncio, 确保测试走内存后端
    - 必须在 repositories.backend / core.locks 模块加载之前执行

Redis 集成测试通过 marker 单独标记, 在 CI 中用独立 job 运行:
    @pytest.mark.redis
"""

import os

# 必须在导入 repositories.backend / core.locks 之前设置
# 否则这两个模块在 import 时会读取到 redis 默认值
os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
