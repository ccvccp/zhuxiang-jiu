# 43号·P5-5 GeoLite2 完整解码(城市/经纬度) 实施计划 v1.0

> 配套：[43号P5_收口增强实施计划.md](43号P5_收口增强实施计划.md) §六
> 定位：P3-3 已交付 mmdb **二分检索层**（IP → 命中偏移，`{resolved: True}`），但数据段解码器缺失——异地跳变当前是**粗口径**（"两小时窗口内 distinct 可定位 IP ≥2"），无法区分"换了同城宽带"与"跨国跳变"。P5-5 补齐 MMDB **数据段解码器**（纯 stdlib 全类型），把跳变检测从"IP 数量"升级为"**真实地理距离**"（>500km/2h），并让 IP 归属地在管理端可见。
> 调研结论（2026-09-03）：
> 1. 检索层现状：`_read_mmdb_search_tree` 已解析元数据（node_count/record_size，24/28/32 bits 三种布局），`lookup_geo` 二分检索到数据段偏移后**只返回命中标记**——数据段起始偏移可由 `node_count × node_size` 直接推算（检索层已有全部参数）；
> 2. `geo_velocity_signal` 粗口径与 `_geo_distance_km` 扩展位均已在 P3-3 注释预留（"完整经纬度距离需数据段解码"）；
> 3. GeoLite2-City 数据结构为嵌套 map（`city.names` / `country.iso_code` / `location.latitude/longitude`），解码需覆盖 MMDB 全类型（pointer/string/double/uint16/uint32/int32/uint64/uint128/array/map/boolean/float）；
> 4. **外部资产待办**：GeoLite2-City.mmdb 需 MaxMind 免费账号下载（P0-P3 既有待办）——本计划用**自建最小 mmdb 测试夹具**（手工构造二进制）实现解码器全类型单测，不依赖外部下载；mmdb 就位即启用。
>
> MMDB 数据段格式速查（MaxMind DB spec v2.0）：
> ```
> control byte: [type(3bit)|size(5bit)]
>   type 1-7 直接编码; type 0 → extended type 再读 1 字节(+128)
>   1=pointer / 2=utf8-string / 3=double / 4=bytes / 5=uint16 /
>   6=uint32 / 7=map / 8=int32 / 9=uint64 / 10=uint128 /
>   11=array / 14=boolean / 15=float
> pointer(1): 前2bit指针大小级别(0-3), 后3bit+后续字节偏移
> string 等: size 0-28 直接长度; 29/30/31 → 0/1/2 个扩展字节
> ```

---

## 一、交付物总览

| 编号 | 内容 | 文件 | 性质 |
|------|------|------|------|
| P5-5a | MMDB 数据段解码器(全类型, 纯 stdlib) | `services/geoip_service.py` | 修改 |
| P5-5b | lookup_geo 结构扩展 + 距离跳变升级 | `services/geoip_service.py` | 修改 |
| P5-5c | 测试夹具(自建最小 mmdb 二进制) + 面板归属地列 | `test_security_p5_5.py` + `js/security-dashboard.js` | 新建/修改 |
| P5-5d | 专项测试+回归+实机+提交 | `test_security_p5_5.py` + `verify_security_p5_5_live.py` | 新建 |

**新增环境变量**：

| 变量 | 默认 | 说明 |
|------|------|------|
| `SECURITY_GEO_VELOCITY_KM` | 500 | 跳变距离阈值(km)——窗口内位移超过此值触发信号 |

---

## 二、P5-5a：MMDB 数据段解码器

### 解码器结构（geoip_service.py 内新增）

```python
# ============================================================
# P5-5: MMDB 数据段解码器(纯 stdlib 全类型)
# ============================================================

def _decode_field(data: bytes, offset: int) -> tuple[Any, int]:
    """解码一个字段 → (value, next_offset)

    全类型覆盖(MaxMind spec):
        1=pointer / 2=utf8-string / 3=double / 4=bytes /
        5=uint16 / 6=uint32 / 7=map / 8=int32 / 9=uint64 /
        10=uint128 / 11=array / 14=boolean / 15=float
    Raises:
        ValueError: 类型未知/长度越界(调用方兜底回退粗口径)
    """
    ctrl = data[offset]
    ftype = ctrl >> 5
    size = ctrl & 0x1F
    offset += 1
    if ftype == 0:   # extended type
        ftype = data[offset] + 128
        offset += 1
    # size 29/30/31 → 扩展长度字节
    if size == 29:
        size = data[offset] + 29; offset += 1
    elif size == 30:
        size = int.from_bytes(data[offset:offset+2], "big") + 285
        offset += 2
    elif size == 31:
        size = int.from_bytes(data[offset:offset+3], "big") + 65821
        offset += 3
    # 按类型解码(见下表)...


def _decode_pointer(data, offset, size) -> tuple[int, int]:
    """指针类型: 前2bit大小级别 → 偏移计算(共4级)"""
    # size>>3 的前2位: 0→1字节(11bit), 1→2字节(19bit),
    #                  2→3字节(27bit), 3→4字节(29bit)


def _decode_data(data: bytes, offset: int) -> dict:
    """数据段入口: 解码顶层 map(GeoLite2 记录)"""
    return _decode_field(data, offset)[0]
```

### 各类型解码口径

| 类型 | 编码 | 解码 |
|------|------|------|
| pointer(1) | 大小级别+偏移 | 数据段内跳转（GeoLite2 用指针共享重复子结构如 `names` map） |
| utf8-string(2) | size=字节长度 | `bytes.decode("utf-8")`（城市名/ISO code） |
| double(3) | 固定 8 字节 | `struct.unpack(">d")`（经度） |
| bytes(4) | size | 原样返回 |
| uint16/32(5/6) | size 字节 | `int.from_bytes(big)`（geoname_id） |
| map(7) | size=键值对数 | 递归交替解码 key(string)+value |
| int32(8) | size 字节 | 符号扩展（气象/海拔可能负值） |
| uint64(9)/uint128(10) | size 字节 | `int.from_bytes` |
| array(11) | size=元素数 | 递归逐元素 |
| boolean(14) | size 即值(0/1) | `bool(size)` |
| float(15) | 固定 4 字节 | `struct.unpack(">f")` |

**设计决策**：

| 决策 | 口径 | 理由 |
|------|------|------|
| **惰性解码** | 命中检索偏移后才解码（未命中零解码开销） | 检索路径性能不变 |
| **一次性解码缓存** | `lookup_geo` 结果按 IP 进程内 LRU（dict，上限 4096 条） | 同 IP 重复查询零开销（网关热路径） |
| **异常兜底** | 解码 ValueError → 返回 `{resolved: True, decode_failed: True}` 粗口径 | fail-soft——解码器 bug 不影响既有检索层 |
| **中文城市名优先** | `names` 取 `zh-CN` 缺省 `en` | 管理端展示口径 |
| **纯 stdlib 延续** | 不引入 maxminddb 依赖 | 与 P3-3 零依赖设计一致 |

---

## 三、P5-5b：lookup_geo 扩展 + 距离跳变升级

### ① lookup_geo 返回结构扩展（向后兼容）

```python
def lookup_geo(ip: str) -> dict | None:
    """IP → 地理信息(离线库; 缺失/未命中返回 None)

    P5-5 扩展(字段缺省时保持向后兼容):
        {ip, resolved: True,
         country_iso: "CN", country_name: "中国",
         city_name: "北京", latitude: 39.9042,
         longitude: 116.4074}
    """
    # 既有二分检索 → 数据段偏移
    # → P5-5: _decode_data 解码(异常回退命中标记口径)
```

### ② 距离跳变（替换粗口径）

```python
def _geo_distance_km(a: dict, b: dict) -> float | None:
    """haversine 公式(纯数学, 无依赖)"""
    import math
    R = 6371.0088   # 地球平均半径(km)
    lat1, lon1 = a["latitude"], a["longitude"]
    lat2, lon2 = b["latitude"], b["longitude"]
    ...标准 haversine...
    return round(distance, 1)


async def geo_velocity_signal(member_id, ip) -> dict:
    """同账号 2h 窗口地理跳变(P5-5 距离口径)

    升级逻辑:
        窗口内最近一次定位(时间戳最新) vs 当前:
            距离 > SECURITY_GEO_VELOCITY_KM(默认500) → 降 50
            + details "北京→上海 1068km/2h"
        无经纬度(解码失败/旧记录无城市) → 回退 P3-3
            粗口径(distinct IP ≥2)——零依赖既有行为
    """
```

- geo 历史存储（`record_member_geo` ZSET member=ip score=时间戳）**零改造**——距离计算时按 score 取最近 IP 再 `lookup_geo`
- 事件留痕增强：factors detail 含 `{from_city} → {to_city} {distance}km`（面板因子展开可读）

### ③ 阈值口径

| 参数 | 默认 | 说明 |
|------|------|------|
| 窗口 | 7200s（2h，既有） | geo 历史 ZSET 滚动窗口 |
| 距离阈值 | 500km | 同城宽带更换 ~50km 不触发；跨省/跨国必触发 |
| 降分 | -50（identity_risk 注入，既有） | 维持 P3-3 强度 |

---

## 四、P5-5c：测试夹具 + 面板展示

### ① 自建最小 mmdb 测试夹具（核心创新点）

不依赖 MaxMind 下载——**手工构造二进制 mmdb** 用于解码器全类型单测：

```python
def _build_test_mmdb(records: list[tuple[str, bytes]]) -> bytes:
    """构造最小 GeoLite2 兼容 mmdb

    结构: [搜索树(最小) | 数据段 | 元数据标记]
    - 搜索树: 每个测试 IP 一条路径(IPv4 32bit 二分)
    - 数据段: 手工编码的 map(覆盖 string/double/uint/map)
    - 元数据: node_count/record_size(24bits)+MaxMind 标记
    """
    # 辅助编码器(测试专用):
    #   _enc_str(s) / _enc_double(x) / _enc_uint(n, size)
    #   _enc_map(pairs) / _enc_ctrl(type, size)
```

- 夹具内预置已知记录：如 `1.2.3.4 → {city: "北京", lat: 39.9042, ...}`、`5.6.7.8 → {city: "上海", lat: 31.2304, ...}`——断言北京-上海距离 ≈1068km（±5% 容差）
- 全类型覆盖：uint128/boolean/float/int32 等构造对应记录逐类型断言

### ② 面板 IP 归属地列（轻量）

- 管理面板④区 IP 处置表新增"归属地"列（`lookup_geo` 城市名，无库显示 `-`）
- **仅列表渲染时调用**（后端 `list_reputations` 返回附加字段），不进网关热路径

---

## 五、测试与验收

### 专项测试（`test_security_p5_5.py`，预计 34 项）

| 分组 | 覆盖 |
|------|------|
| 编码器(夹具地基) | ctrl 编码/size 扩展(29/30/31)三档/extended type/各类型 round-trip |
| 解码器全类型 | utf8-string(含中文) / double / uint16/32 / uint64 / uint128 / int32(负值) / boolean / float / bytes / array / map(嵌套) / pointer(4 级大小) |
| lookup_geo 扩展 | 夹具 IP → 城市名/经纬度/ISO code / 未命中 None / 中文优先 / 解码异常回退 resolved 口径 |
| haversine | 北京-上海 1068km±5% / 同点 0km / 对跖点 ~20015km |
| 距离跳变 | 2h 窗口 500km+ 触发降 50 / 500km 内不触发 / 无经纬度回退粗口径 / 阈值环境变量生效 |
| 留痕 | factors 含城市对+距离 / details 可读 |
| 面板数据源 | list_reputations 归属地字段 / 无库显示 - |
| 回归 | 无库(缺文件)静默关闭零影响 / 既有检索层行为不变 |

### 实机验收（`verify_security_p5_5_live.py`，预计 10 项）

1. 正常业务零影响
2. 容器无 mmdb：geo 静默关闭（`geo_available()=False`）零影响路径
3. **夹具注入**：自建 mmdb 写入容器 `./data/` → `geo_available()=True`
4. lookup_geo 容器内真实解码（夹具 IP → 北京/上海城市名+经纬度）
5. 距离跳变 E2E：会员先经"北京 IP"请求 → 再经"上海 IP" → geo_velocity 信号触发（距离 1068km>500）
6. 同城宽带（夹具两个近距 IP）不触发
7. 事件留痕含城市对+距离
8. 面板 IP 列表归属地字段
9. 清理夹具 mmdb → 恢复静默关闭
10. 全程业务正常

> real GeoLite2（MaxMind 下载）就位后无需任何代码变更——夹具与真实库同格式，仅替换 `./data/GeoLite2-City.mmdb` 文件。

### 回归范围

- security 全系列 17 套（16 既有 + P5-4 新增）
- 无 mmdb 环境下全量回归（geo 路径零影响验证）

---

## 六、关键风险与对策

| 风险 | 对策 |
|------|------|
| 数据段格式复杂（指针压缩/变长编码） | 对照官方 spec 逐类型单测 + 夹具手工编码双向验证（编码器→解码器 round-trip） |
| 解码 bug 影响网关热路径 | 惰性解码+异常兜底（回退 P3-3 命中标记口径）+ IP 级 LRU 缓存（4096 条） |
| 检索层元数据口径不准（P3-3 简化解析） | 夹具用标准 24bits 布局验证；真实库若 node_count 解析偏差 → `_read_mmdb_search_tree` 抛错静默关闭（既有兜底） |
| mmdb 外部资产未就位 | 夹具先行全链路交付；mmdb 就位即用（零代码变更） |
| 距离阈值误杀（高铁/飞机通勤） | 默认 500km 保守 + 阈值环境变量可调 + 仅降分不处置（observe 口径不变） |

---

## 七、里程碑

| 阶段 | 交付 | 验收门槛 |
|------|------|---------|
| P5-5a | 数据段解码器(全类型) | 编码/解码 round-trip 全绿 |
| P5-5b | lookup_geo 扩展+距离跳变 | 夹具 E2E(北京→上海触发/同城不触发) |
| P5-5c | 面板归属地列 | 数据源字段+渲染 |
| P5-5d | 收官 | 专项 34 项+security 回归 17/17+实机 10 项+提交推送 |

---

## 八、外部待办（P5-5 后真实数据启用）

1. **MaxMind 免费账号注册**（https://www.maxmind.com/ → GeoIP2 / GeoLite2 下载 GeoLite2-City.mmdb）
2. mmdb 放宿主机 `./data/GeoLite2-City.mmdb`（compose 已挂载 `./data:/app/data:ro`）
3. 无下载不阻塞交付——夹具全链路先行（本计划核心设计）

---

*P5-5 计划（2026-09-03）：解码器是 P3-3 检索层的"最后一公里"——自建夹具解耦外部资产（mmdb 就位零代码变更）、惰性解码+LRU 保护热路径、距离口径替换 IP 数量口径（真实地理语义）、fail-soft 全链路兜底（解码异常回退粗口径），四项设计保证增强不引入回归面。*
