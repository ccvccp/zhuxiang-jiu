# 43号·P5-6 CIDR 区间二分检索 实施计划 v1.0

> 配套：[43号P5_收口增强实施计划.md](43号P5_收口增强实施计划.md) §七
> 定位：P4-3 `match_threatintel` 为**线性扫描**（每查询对每段构造 `ipaddress.ip_network` 对象再 `in` 判断）——firehol_level1 约 2k 段可接受；P5-3 自动订阅稳定后若聚合多源（firehol_level2+ 约 20k 段 / 全量聚合约 180k 段），每请求 O(n) 不可接受。P5-6 把匹配从 O(n) 降到 **O(log n)**（区间二分），阈值自动分流——小规模保持线性（省构建成本），大规模自动切换。
> 调研结论（2026-09-03）：
> 1. **线性扫描的双重开销**：①每查询×每段构造 `ip_network` 对象（2k 段=每请求 2k 次对象构造）；②Redis 模式下 `list_threatintel()` 本身是 KEYS + 逐键 hgetall——20k 段时**每次匹配查询伴随 2 万次网络往返**，这比算法复杂度更先成为瓶颈。区间缓存顺带消解第二重开销（构建一次，查询零 list 调用）；
> 2. 调用方仅 `threatintel_service.check_ip`（网关 `apply_to_reputation` + `/admin/threatintel/check` 端点）——仓储层改造**对上层完全透明**，零路由/服务改动；
> 3. 方案选型——**区间二分替代前缀树**（主计划既定）：标准库 `bisect`、无自定义节点结构、内存紧凑（两个 int 列表）、v4/v6 通用（128 位 int）；实现简单度与可测性远优于前缀树；
> 4. 触发条件现状：firehol_level1 约 2k 段 → **交付能力但不强制生效**（阈值 ≥1000 段自动启用二分，2k 段即已进入二分区间；若仅 level1 规模收益约 10×，聚合多源后收益 100×+）。

---

## 一、交付物总览

| 编号 | 内容 | 文件 | 性质 |
|------|------|------|------|
| P5-6a | 区间二分核心（构建/查询/命中回填） | `repositories/security_repository.py` | 修改 |
| P5-6b | 阈值分流 + 版本戳缓存失效 | `repositories/security_repository.py` | 修改 |
| P5-6c | 可观测性（stats.matchMode） | `services/threatintel_service.py` | 修改 |
| P5-6d | 专项+性能基准+回归+实机+提交 | `test_security_p5_6.py` + `verify_security_p5_6_live.py` | 新建 |

**无新增环境变量**（阈值 1000 为代码常量，不做配置面——口径见 §三）。

---

## 二、P5-6a：区间二分核心

### 数据结构（仓储层模块级单例缓存）

```python
# repositories/security_repository.py 模块级
_TI_RANGE_CACHE: tuple | None = None   # (version, v4_intervals, v6_intervals)
_TI_VERSION = 0                        # 版本戳(save/clear 递增)

# 区间条目: (start_int, end_int, cidr_str)
#   v4: 32 位 int; v6: 128 位 int —— 两族独立列表
#   start = int(net.network_address)
#   end   = int(net.broadcast_address)
```

### 算法（构建 O(n log n) + 查询 O(log n)）

```python
async def _build_range_cache(self) -> tuple:
    """构建有序区间表(懒构建, 导入后首次匹配触发)

    来源: list_threatintel() 一次全量读取
      - 解析 CIDR → (start_int, end_int, cidr)
      - 非法段跳过(导入校验兜底, 不中断构建)
      - 按 start 升序排序(两族各自)
    命中回填: 区间条目只存 cidr 字符串——命中时
    get_threatintel(cidr) 单键 hgetall 取回完整记录
    (省 180k 段 × dict 的内存; 命中是稀有路径)
    """
    records = await self.list_threatintel()
    v4, v6 = [], []
    for r in records:
        cidr = str(r.get("actorKey", ""))[len("threatintel:"):]
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            start = int(net.network_address)
            end = int(net.broadcast_address)
            entry = (start, end, cidr)
            (v4 if net.version == 4 else v6).append(entry)
        except ValueError:
            continue
    v4.sort(key=lambda e: e[0])
    v6.sort(key=lambda e: e[0])
    return (_TI_VERSION, v4, v6)


async def match_threatintel(self, ip: str) -> dict | None:
    """IP 命中情报段(阈值分流: ≥1000 段区间二分 / 其余线性)"""
    addr = ipaddress.ip_address(ip)          # 非法 IP 仍 None
    if 规模 < 1000:
        return await self._match_linear(...)  # 既有路径原样保留
    # 二分: bisect_right(starts, ip_int) 取左邻区间
    #   左邻区间 start <= ip_int 且 end >= ip_int → 命中
    #   (区间可能重叠——情报段理论不重叠, 重叠时返回
    #    start 最大的左邻, 与线性首个命中语义一致性
    #    由专项测试全量对比保证)
```

### 关键设计决策

| 决策 | 口径 | 理由 |
|------|------|------|
| **命中回填单键取** | 区间只存 cidr，命中时 `get_threatintel(cidr)` 取完整记录 | 180k 段全 dict 缓存约 50MB+；只存 int+str 约 3MB；命中（恶意 IP）是稀有路径，单键开销可忽略 |
| **v4/v6 双列表** | 两族独立区间表，查询按 `addr.version` 选表 | 32 位与 128 位 int 混排无法单调排序；分表各自有序 |
| **模块级缓存** | 单例（跨 repo 实例共享——ThreatIntelService 每次 new repo） | 缓存是纯计算产物，与存储模式无关（双模式通用） |
| **懒构建** | 导入后首次匹配触发，导入过程零重建 | P5-3 导入 150~20k 次逐段 save 不触发任何构建 |

---

## 三、P5-6b：阈值分流 + 版本戳失效

### 阈值分流（常量 `RANGE_THRESHOLD = 1000`）

```
段数 < 1000  → 线性（既有路径零改动——省 O(n log n) 构建成本）
段数 ≥ 1000  → 区间二分（firehol_level1 约 2k 段即已启用）
```

不做环境变量的理由：阈值是纯性能内部参数（非运维口径），配置面留给数据规模说话——段数本身就是唯一正确的开关信号。

### 版本戳失效（防陈旧缓存的三个陷阱）

```python
# save_threatintel / clear_threatintel 末尾:
global _TI_VERSION
_TI_VERSION += 1

# match_threatintel 二分路径开头:
#   if _TI_RANGE_CACHE is None or _TI_RANGE_CACHE[0] != _TI_VERSION:
#       _TI_RANGE_CACHE = await self._build_range_cache()
```

| 陈旧陷阱 | 版本戳如何防御 |
|---------|---------------|
| 同规模换内容（周度幂等刷新导入 2000 段不同段，count 不变） | 每次 save/clear 递增版本 → 必重建 |
| 导入中途查询（clear 后 save 到一半） | save 已递增版本 → 查询触发重建（读半量数据，安全——导入完成后的下次查询再次重建） |
| 增量导入（P5-3 replace=False 追加） | save 递增版本 → 必重建 |

asyncio 单线程模型天然免疫构建竞态；版本戳只防"陈旧"不防"并发"（后者不存在）。

---

## 四、P5-6c：可观测性（stats.matchMode）

`stats()` 增加 `matchMode` 字段（`"linear"` / `"bisect"`）+ `matchSegments`（当前段数）——面板/运维可见当前匹配策略与规模，聚合多源时可直接确认二分已生效。

---

## 五、测试与验收

### 专项测试（`test_security_p5_6.py`，预计 22 项）

| 分组 | 覆盖 |
|------|------|
| 正确性全量对比 | 随机 1000 组 IP×段集：二分结果与线性结果**逐组全等**（含命中/未命中） |
| 边界 | 段首 IP/段尾 IP 命中 / 段外 ±1 不命中 / 单 IP 段（/32）/ 相邻段无缝衔接 |
| v6 | v6 段命中/不命中 / v4 查询不误入 v6 表（反之亦然） |
| 阈值分流 | 999 段走线性 / 1000 段走二分 / 两路径行为一致（同数据双跑对比） |
| 缓存失效 | 导入新段后命中更新 / 同规模换内容必重建（陈旧陷阱回归）/ clear 后未命中 / 增量导入生效 |
| stats | matchMode/segments 字段两态 |
| 性能基准 | 20k 段 + 10k 随机查询 **< 200ms**（二分）；线性对照采样 100 查询，二分全程 < 线性采样耗时（量化收益断言） |

### 实机验收（`verify_security_p5_6_live.py`，预计 10 项）

1. 正常业务零影响
2. 导入 20k 生成段（10.q.r.0/24 全空间）
3. stats.matchMode=bisect + segments=20000
4. 命中查询正确（段内/段外/边界）
5. **响应时间可接受**（10k 次命中查询容器内总耗时 < 2s，含 Redis）
6. 陈旧陷阱回归：同规模 20k 段**换内容**重新导入 → 命中随新数据更新
7. 幂等刷新：同内容再导入 → 行为不变
8. 清空 → matchMode=linear + 命中恢复 None
9. 网关全链路回归（命中降档 31 联动正常）
10. 业务正常

### 回归范围

- security 全系列 18 套（17 既有 + P5-5 新增）
- 重点：P4-3 威胁情报专项（线性既有口径不回归破坏）

---

## 六、关键风险与对策

| 风险 | 对策 |
|------|------|
| 二分与线性结果不一致（重叠段语义） | 专项 1000 组随机全量对比逐组断言相等；理论段集不重叠（导入源为 netset 标准格式） |
| 构建大库耗时（180k 段 list 全量读） | 懒构建一次 O(n log n)；构建期间请求走线性（首次触发时的单次慢查询可接受——或直接构建后生效） |
| 版本戳漏 increment（新写入路径） | 写入路径收敛为 save/clear 两个仓储方法（grep 校验无第三写入点）；专项含陈旧陷阱回归 |
| 内存（180k 段 × (2 int + str)） | 约 3MB 可接受；命中回填单键取避免 50MB dict 缓存 |
| 误配超大导入（P4-3 上限 2 万段内） | MAX_IMPORT_CIDRS=20000 既有护栏——20k 段是当前实际上限，180k 需先调上限（另行评估，不在本期） |

---

## 七、里程碑

| 阶段 | 交付 | 验收门槛 |
|------|------|---------|
| P5-6a | 区间二分核心（构建/查询/回填） | 边界+正确性组全绿 |
| P5-6b | 阈值分流+版本戳 | 失效三陷阱回归全绿 |
| P5-6c | stats.matchMode | 可观测字段 |
| P5-6d | 收官 | 专项 22 项+性能基准达标+security 回归 18/18+实机 10 项+提交推送 |

---

## 八、后续方向（不在本期）

- MAX_IMPORT_CIDRS 上限评估（聚合全量源 180k 段时另行提额——需同步评估构建耗时与内存）
- 情报源聚合策略（firehol_level2+/cross/refined 多源合并，P5-3 双轨基建已就绪）

---

*P5-6 计划（2026-09-03）：P5 收官之作——纯算法优化零外部依赖：区间二分（bisect 标准库）+ 阈值自动分流（规模即开关）+ 版本戳防陈旧（三陷阱回归）+ 命中回填单键取（内存 3MB 而非 50MB），对上层完全透明（零路由/服务改动），线性路径原样保留（小规模零成本）。至此 P5 六个方向全部排期完毕。*
