# 测试运行规范（双通道自举 + Bearer 约定）

> 来源：AUTH-TEST-01 立项 P2 制度固化（2026-10-03）
> 关联：backend/test_support.py / backend/conftest.py /
> backend/test_auth_support.py /
> docs/auth中间件XRole直连403_独立立项方案.md

---

## 一、双通道与同源自举（铁则）

项目测试存在两种运行方式，环境配置必须**同源自举**：

| 通道 | 运行 | 环境来源 |
|---|---|---|
| pytest | `pytest test_xxx.py` | conftest.py 自动设置 |
| 直跑（项目主流惯例） | `python test_xxx.py` | **首行 `import test_support`** |

**教训（教训汇编第十七条）**：conftest 只在 pytest 启动时加载，
直跑脚本不经 conftest——缺自举会导致：
- `AUTH_COMPAT_TRUST_HEADERS` 未设 → X-Role 被中间件剥离 → 管理端点全 403
- `STORE_MODE/LOCK_MODE` 落回 redis 默认 → 测试静默误连开发 Redis

## 二、test_support.py 三环境变量

| 变量 | 测试值 | 语义 |
|---|---|---|
| LOCK_MODE | asyncio | 并发锁走内存 |
| STORE_MODE | asyncio | 存储走内存（_mock_store） |
| AUTH_COMPAT_TRUST_HEADERS | 1 | 测试信任旧头（裸头 X-Role 通道） |

均为 `setdefault`：显式环境优先（redis 集成测试/CI 不受影响）；
**仅测试进程内生效，生产 .env 从未设置**。

## 三、鉴权三轨（写测试时选轨）

| 轨 | 写法 | 适用 |
|---|---|---|
| **Bearer（新测试默认）** | `test_support.mint_token(role)` → `Authorization: Bearer <t>` | 与生产同构，strict 模式也覆盖 |
| 裸头 X-Role（存量通道） | `headers={"X-Role": "admin"}` | 仅存量测试，**不再新增** |
| 服务层直调 | 直接调 service 函数 | 不关心 HTTP 层时 |

铸 token：`mint_token("admin")` / `mint_token("member")`
（默认号段 13800000901/902，register→login 幂等）。

**Bearer 迁移三个易踩点（首批/第二批迁移实证 2026-10-03）**：
1. **空库断言互斥**——断言"库为空/会员数 0"的阶段不能用 Bearer
   （真实会员行必然计入），该阶段回退裸头轨（三轨并存的本意）
2. **计数敏感测试**——新增铸 token 会员会扰动全站计数断言；
   应提权**种子内既有会员**（`role="admin"` + `create_token(mid,
   role="admin")`，参照 test_zhike.seed_admin_bearer），
   且凡 `reset_store()/clear_all()` 清库点之后需重铸
3. **异步上下文禁铸造**——mint_token 内部 asyncio.run，不可在
   async 函数内调用（嵌套禁止）；多段测试（服务层段清库）应在
   清库段之后、下一 asyncio.run 之前于同步 main() 中铸造
   （参照 test_zw_mode.main）

## 四、新测试文件模板

```python
"""<模块> 专项测试
运行: python test_xxx.py
"""
import test_support  # noqa: F401 (直跑自举: 内存模式; 首行约定)

import sys


def main():
    token = test_support.mint_token(role="admin")
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    r = client.get("/api/xxx", headers={"Authorization": f"Bearer {token}"})
    ...


if __name__ == "__main__":
    sys.exit(main())
```

要点：
1. `import test_support` 必须在 repositories/main 等业务模块导入**之前**
2. 模块三态灰度开档：需决策面时在头部 `os.environ["X_MODE"] = "assist"`
   （off 默认会 409，参见 test_zhike.py 补档案例）
3. 测试内临时切换 AUTH_MODE/头信任后**必须恢复**（同进程污染防护，
   参照 test_auth_support.py 尾部）

## 五、鉴权语义常驻回归

`test_auth_support.py`（12 断言）覆盖四组语义，鉴权相关改动后必跑：
compat+头信任 / compat+默认剥离 / member 伪造头 403 / strict 401 全组。

## 六、CI 预留

redis 集成测试用 `@pytest.mark.redis` 标记，CI 中独立 job 运行
（conftest.py 既有约定）；当前无 CI 环境，引入时按此分流。
