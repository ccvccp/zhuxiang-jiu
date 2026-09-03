"""43号·P3-3 GeoIP 离线库 + 设备指纹服务(geo_device 因子精细化)
        + P5-5 MMDB 数据段完整解码(城市/经纬度/距离跳变)

计划 §四(docs/43号P3_纵深增强实施计划.md):
    - 设备指纹: 复用 39号 entry_devices 信任设备表(X-Device-Id 头,
      陌生设备打敏感端点 → identity_risk 降分; 同账号 24h 设备数
      ≥3 撞库信号)
    - GeoIP: GeoLite2-City.mmdb 离线库(缺失静默关闭零影响),
      同账号两小时内跨省 IP 变更 → 异地跳变信号(对齐 30号
      auth_risk 的 geo_velocity 概念)
    - XFF 反代头: X-Forwarded-For 第一跳(云部署生效)

P5-5 增强(docs/43号P5-5_GeoLite2完整解码实施计划.md):
    - 数据段解码器: 全类型(pointer/string/double/uint16/32/64/
      128/int32/array/map/boolean/float), 纯 stdlib 零依赖延续
    - lookup_geo 返回完整地理信息(city/country/经纬度, 中文优先)
    - 距离跳变: haversine 真实地理距离(>500km/2h)替换
      P3-3"distinct IP 计数"粗口径; 解码失败回退粗口径(fail-soft)
    - 惰性解码 + IP 级 LRU(4096)保护网关热路径
    - 自建最小 mmdb 夹具全链路测试(真实库就位零代码变更)

mmdb 解析: 纯 stdlib 实现(检索 + P5-5 完整数据段解码),
不引入外部依赖; 文件缺失时 geo 功能整体静默关闭(零影响路径)。
"""

import logging
import os
import struct

logger = logging.getLogger(__name__)

GEOIP_DB_PATH = os.environ.get(
    "GEOIP_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "GeoLite2-City.mmdb"))

# 异地跳变窗口与阈值(P3-3: 两小时窗口; P5-5: 距离阈值)
GEO_VELOCITY_WINDOW = 7200
GEO_VELOCITY_KM_DEFAULT = 500.0
# 设备数堆积阈值(24h ≥3 撞库信号)
DEVICE_COUNT_THRESHOLD = 3

# P5-5: lookup_geo 结果 LRU(进程内; 容量超限整体清空——
# 简单口径, 网关热路径防重复解码)
_GEO_LRU: dict = {}
_GEO_LRU_MAX = 4096

_MMDB_METADATA_MARKER = b"\xab\xcd\xefMaxMind.com"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _geo_velocity_km() -> float:
    """距离跳变阈值(km, 默认 500——同城宽带更换 ~50km 不误杀)"""
    try:
        return float(_env("SECURITY_GEO_VELOCITY_KM",
                          str(GEO_VELOCITY_KM_DEFAULT)))
    except ValueError:
        return GEO_VELOCITY_KM_DEFAULT


# ============================================================
# P5-5: MMDB 数据段解码器(MaxMind DB spec v2.0 全类型, 纯 stdlib)
# ============================================================

def _decode_field(data: bytes, offset: int) -> tuple:
    """解码一个字段 → (value, next_offset)

    ctrl byte: [type(3bit) | size(5bit)]
        type 1-7 直接; type 0 → extended(下一字节+7)
        size 29/30/31 → 1/2/3 个扩展长度字节(0-based 减基数)
    类型: 1=pointer / 2=utf8-string / 3=double / 4=bytes /
        5=uint16 / 6=uint32 / 7=map / 8=int32 / 9=uint64 /
        10=uint128 / 11=array / 14=boolean / 15=float
    Raises:
        ValueError: 类型未知/长度越界(调用方兜底回退粗口径)
    """
    if offset >= len(data):
        raise ValueError("数据段越界")
    ctrl = data[offset]
    ftype = ctrl >> 5
    size = ctrl & 0x1F
    offset += 1
    if ftype == 0:   # extended type
        if offset >= len(data):
            raise ValueError("ext type 越界")
        ftype = data[offset] + 7
        offset += 1
    # size 扩展(29/30/31 → 1/2/3 字节, 基数 29/285/65821)
    if size == 29:
        size = data[offset] + 29
        offset += 1
    elif size == 30:
        size = int.from_bytes(data[offset:offset + 2], "big") + 285
        offset += 2
    elif size == 31:
        size = int.from_bytes(
            data[offset:offset + 3], "big") + 65821
        offset += 3

    if ftype == 1:   # pointer(前2bit大小级别, 低3bit偏移高位)
        level = size >> 3
        ptr_bits = size & 0x07
        if level == 0:
            ptr = (ptr_bits << 8) | data[offset]
            offset += 1
        elif level == 1:
            ptr = (ptr_bits << 16) | int.from_bytes(
                data[offset:offset + 2], "big")
            offset += 2
        elif level == 2:
            ptr = (ptr_bits << 24) | int.from_bytes(
                data[offset:offset + 3], "big")
            offset += 3
        else:
            ptr = int.from_bytes(data[offset:offset + 4], "big")
            offset += 4
        # 指针目标递归解码(数据段内共享子结构)
        value, _ = _decode_field(data, ptr)
        return value, offset

    if ftype == 2:   # utf8-string
        end = offset + size
        if end > len(data):
            raise ValueError("string 越界")
        return data[offset:end].decode("utf-8", "replace"), end
    if ftype == 3:   # double(固定 8 字节)
        return struct.unpack(">d", data[offset:offset + 8])[0], \
            offset + 8
    if ftype == 4:   # bytes
        end = offset + size
        if end > len(data):
            raise ValueError("bytes 越界")
        return data[offset:end], end
    if ftype in (5, 6):   # uint16 / uint32
        end = offset + size
        if end > len(data):
            raise ValueError("uint 越界")
        return int.from_bytes(data[offset:end], "big"), end
    if ftype == 7:   # map(size=键值对数)
        result = {}
        for _ in range(size):
            key, offset = _decode_field(data, offset)
            value, offset = _decode_field(data, offset)
            result[key if isinstance(key, str) else str(key)] = value
        return result, offset
    if ftype == 8:   # int32(补码)
        end = offset + size
        if end > len(data):
            raise ValueError("int32 越界")
        return int.from_bytes(data[offset:end], "big",
                              signed=True), end
    if ftype in (9, 10):   # uint64 / uint128
        end = offset + size
        if end > len(data):
            raise ValueError("uint64/128 越界")
        return int.from_bytes(data[offset:end], "big"), end
    if ftype == 11:   # array(size=元素数)
        result = []
        for _ in range(size):
            value, offset = _decode_field(data, offset)
            result.append(value)
        return result, offset
    if ftype == 14:   # boolean(size 即值)
        return bool(size), offset
    if ftype == 15:   # float(固定 4 字节)
        return struct.unpack(">f", data[offset:offset + 4])[0], \
            offset + 4
    raise ValueError(f"未知字段类型 {ftype}")


# ============================================================
# mmdb 检索层(P3-3 建, P5-5 修正 metadata 解析与数据段出口)
# ============================================================

def _read_mmdb_search_tree(path: str):
    """加载 mmdb 元数据与搜索树(懒加载单例)

    GeoLite2 格式: [搜索树 | 数据段 | 元数据标记段]
    返回 (file_bytes, node_count, record_size_bits)
    或 None(文件缺失/格式不符 → geo 功能静默关闭)。

    P5-5: metadata 段本身是 MMDB 数据格式 map——用解码器
    正确解析 node_count/record_size(真实库口径), 解析失败
    回退 P3-3 简化口径(固定偏移)再试, 双失败静默关闭。
    """
    global _MMDB_CACHE
    if _MMDB_CACHE is not None:
        return _MMDB_CACHE if _MMDB_CACHE != "DISABLED" else None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        logger.info("geoip_db_missing path=%s (geo 功能静默关闭)",
                    path)
        _MMDB_CACHE = "DISABLED"
        return None
    try:
        meta_start = data.rfind(_MMDB_METADATA_MARKER)
        if meta_start < 0:
            raise ValueError("无 MaxMind 元数据标记")
        node_count = None
        record_size_bits = None
        # P5-5 正确口径: 解码器解析 metadata map
        try:
            meta, _ = _decode_field(
                data, meta_start + len(_MMDB_METADATA_MARKER))
            if isinstance(meta, dict):
                node_count = int(meta.get("node_count") or 0)
                rs = int(meta.get("record_size") or 0)
                if rs in (24, 28, 32):
                    record_size_bits = rs
        except (ValueError, IndexError, TypeError):
            pass
        # 回退 P3-3 简化口径(固定偏移——夹具/旧格式兼容)
        if not node_count or record_size_bits is None:
            node_count = struct.unpack(
                ">I", data[meta_start + 16:meta_start + 20])[0]
            record_size_bits = data[meta_start + 22]
            if record_size_bits not in (24, 28, 32):
                record_size_bits = 24
        if not node_count or node_count > 2 ** 28:
            raise ValueError(f"node_count 非法 {node_count}")
        _MMDB_CACHE = (data, node_count, record_size_bits)
        logger.info("geoip_db_loaded path=%s nodes=%d rs=%d",
                    path, node_count, record_size_bits)
        return _MMDB_CACHE
    except Exception as exc:
        logger.warning("geoip_db_invalid path=%s: %s (静默关闭)",
                       path, exc)
        _MMDB_CACHE = "DISABLED"
        return None


_MMDB_CACHE = None


def _reset_geoip_cache() -> None:
    """重置 mmdb 懒加载缓存与 LRU(测试/换库时用)"""
    global _MMDB_CACHE
    _MMDB_CACHE = None
    _GEO_LRU.clear()


def _ip_to_bytes(ip: str) -> bytes | None:
    """IPv4 → 16 字节(前 12 字节 0 = ::/96 IPv4 映射,
    符合 MaxMind spec 的 IPv4 地址遍历口径; IPv6 不支持返回
    None——GeoLite2 离线子集仅 IPv4)"""
    try:
        parts = ip.strip().split(".")
        if len(parts) != 4:
            return None
        return b"\x00" * 12 + bytes(
            int(p) & 0xFF for p in parts)
    except (ValueError, AttributeError):
        return None


def _geo_cache_put(ip: str, result: dict) -> None:
    """LRU 简单口径: 容量超限整体清空"""
    if len(_GEO_LRU) >= _GEO_LRU_MAX:
        _GEO_LRU.clear()
    _GEO_LRU[ip] = result


def lookup_geo(ip: str) -> dict | None:
    """IP → 地理信息(离线库; 缺失/未命中返回 None)

    P5-5 完整口径(字段缺省时向后兼容 P3-3):
        {ip, resolved: True,
         country_iso: "CN", country_name: "中国",
         city_name: "北京", latitude: 39.9042,
         longitude: 116.4074}
    解码异常 → {ip, resolved: True, decode_failed: True}
    (fail-soft 回退 P3-3 命中标记粗口径)。
    """
    if not ip or ip in ("unknown", "127.0.0.1", "0.0.0.0"):
        return None
    cached = _GEO_LRU.get(ip)
    if cached is not None:
        return cached
    loaded = _read_mmdb_search_tree(GEOIP_DB_PATH)
    if loaded is None:
        return None
    data, node_count, record_bits = loaded
    addr = _ip_to_bytes(ip)
    if addr is None:
        return None
    # 二分检索(128 bit: 前 96 bit 0 走 IPv4 子树;
    # 每节点 2 条记录, record_bits 位/条)
    node_bytes = record_bits * 2 // 8
    node = 0
    for byte in addr:
        if node >= node_count:
            break
        for bit in range(7, -1, -1):
            if node >= node_count:
                break   # 数据段记录
            b = (byte >> bit) & 1
            offset = node * node_bytes
            if record_bits == 24:
                left = struct.unpack(
                    ">I", b"\x00" + data[offset:offset + 3])[0]
                right = struct.unpack(
                    ">I", b"\x00" +
                    data[offset + 3:offset + 6])[0]
            else:
                # 28/32 bits: 左右记录跨界拼装
                raw = data[offset:offset + node_bytes]
                left = (raw[0] << 20) | (raw[1] << 12) | \
                    (raw[2] << 4) | (raw[3] >> 4)
                right = ((raw[3] & 0x0F) << 24) | \
                    (raw[4] << 16) | (raw[5] << 8) | raw[6]
            node = right if b else left
    if node == node_count:
        return None   # 未命中(空记录)
    # 数据段记录: offset = 记录值 - node_count - 1
    # (== node_count 为 miss; 数据段偏移从 0 起)
    data_offset = node - node_count - 1
    tree_size = node_count * node_bytes
    try:
        record, _ = _decode_field(data, tree_size + data_offset)
    except Exception:
        logger.warning("geoip_decode_failed ip=%s offset=%s",
                       ip, data_offset)
        result = {"ip": ip, "resolved": True,
                  "decode_failed": True}
        _geo_cache_put(ip, result)
        return result
    result = {"ip": ip, "resolved": True}
    if isinstance(record, dict):
        city = record.get("city") or {}
        country = record.get("country") or {}
        location = record.get("location") or {}
        names = city.get("names") or {}
        # 中文城市名优先(zh-CN 缺省 en)
        result["city_name"] = (
            names.get("zh-CN") or names.get("en"))
        cn_names = country.get("names") or {}
        result["country_iso"] = country.get("iso_code")
        result["country_name"] = (
            cn_names.get("zh-CN") or cn_names.get("en"))
        lat = location.get("latitude")
        lon = location.get("longitude")
        result["latitude"] = (
            float(lat) if isinstance(lat, (int, float)) else None)
        result["longitude"] = (
            float(lon) if isinstance(lon, (int, float)) else None)
    _geo_cache_put(ip, result)
    return result


def geo_available() -> bool:
    """geo 离线库是否可用(不可用时因子退化为纯时段口径)"""
    return _read_mmdb_search_tree(GEOIP_DB_PATH) is not None


# ============================================================
# P5-5: haversine 地理距离(纯数学, 无依赖)
# ============================================================

def _geo_distance_km(a: dict, b: dict) -> float | None:
    """两点球面距离(haversine, 地球平均半径 6371.0088km)

    任一点缺经纬度 → None(调用方回退粗口径)
    """
    import math
    try:
        lat1 = math.radians(float(a["latitude"]))
        lon1 = math.radians(float(a["longitude"]))
        lat2 = math.radians(float(b["latitude"]))
        lon2 = math.radians(float(b["longitude"]))
    except (KeyError, TypeError, ValueError):
        return None
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2)
         * math.sin(dlon / 2) ** 2)
    return round(2 * 6371.0088 * math.asin(math.sqrt(h)), 1)


# ============================================================
# XFF 反代头解析
# ============================================================

def extract_client_ip(xff: str, direct_ip: str) -> str:
    """客户端真实 IP: X-Forwarded-For 第一跳优先, 缺省直连地址

    直连容器场景(127.0.0.2)无 XFF, 行为不变; 云部署反代生效。
    """
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return direct_ip


# ============================================================
# 设备指纹(复用 39号 entry_devices 信任设备表)
# ============================================================

async def device_risk_signal(member_id: int, device_id: str,
                              path: str) -> dict:
    """设备指纹风险信号(无 39号指纹基建知识时的中性口径)

    Returns:
        {score(0-100, 低=风险), details: [...], hasSignal: bool}
        - 陌生设备打敏感端点 → 降 60(仅 admin/finance 前缀)
        - hasSignal=False: 无指纹头或无风险(中性, 不参与降分)
    """
    device_id = str(device_id or "").strip()
    if not member_id or not device_id:
        return {"score": 100.0, "details": [], "hasSignal": False}
    details = []
    score = 100.0
    try:
        from repositories.entry_repository import EntryRepository
        repo = EntryRepository()
        device = await repo.get_device(int(member_id), device_id)
        is_trusted = device is not None
        sensitive = path.startswith(("/api/admin/", "/api/finance/"))
        if not is_trusted and sensitive:
            # 陌生设备打敏感端点: 39号登录时已信任此设备的记录
            # 不在表中 → 降分(疑似新设备会话被劫持后直奔敏感面)
            score -= 60.0
            details.append("陌生设备访问敏感端点")
        # 同账号 24h 设备数(信任设备数异常膨胀 = 撞库信号)
        trusted_count = 0
        try:
            for record in (await repo.list_devices(int(member_id))
                           or []):
                trusted_count += 1
                if trusted_count >= DEVICE_COUNT_THRESHOLD:
                    break
        except AttributeError:
            pass   # list_devices 不可用时不惩罚
        if trusted_count >= DEVICE_COUNT_THRESHOLD:
            score -= 40.0
            details.append(f"信任设备数≥{DEVICE_COUNT_THRESHOLD}"
                           "(撞库信号)")
        return {"score": max(0.0, score), "details": details,
                "hasSignal": bool(details)}
    except ImportError:
        # 39号仓储不可用(理论上不发生): 中性
        return {"score": 100.0, "details": [], "hasSignal": False}


# ============================================================
# 异地跳变(geo velocity, 对齐 30号 auth_risk 概念)
# ============================================================

async def geo_velocity_signal(member_id: int, ip: str) -> dict:
    """同账号两小时窗口地理跳变检测(离线库可用时)

    P5-5 距离口径(优先):
        窗口内最近一次定位 vs 当前 → haversine 距离 >
        SECURITY_GEO_VELOCITY_KM(默认 500) → 降 50
        + details "北京→上海 1068km/2h"
    回退 P3-3 粗口径: 无经纬度(解码失败/旧记录) →
        distinct 可定位 IP ≥2 降 50(fail-soft 零依赖既有行为)

    Returns:
        {score, details, hasSignal}
    """
    if not member_id or not geo_available():
        return {"score": 100.0, "details": [], "hasSignal": False}
    details = []
    score = 100.0
    try:
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()
        history = await repo.get_member_geo_history(member_id)
        current = lookup_geo(ip)
        if current and history:
            # P5-5 距离口径: 取窗口内最新有经纬度的定位
            # (ZSET score 升序返回, 末位最新)与当前比距离
            last_geo = None
            for h_ip in history:
                g = lookup_geo(h_ip)
                if g and g.get("latitude") is not None:
                    last_geo = g
            if last_geo and current.get("latitude") is not None:
                dist = _geo_distance_km(last_geo, current)
                if dist is not None and dist > _geo_velocity_km():
                    score -= 50.0
                    from_city = (last_geo.get("city_name")
                                 or last_geo.get("ip") or "?")
                    to_city = (current.get("city_name")
                               or ip or "?")
                    details.append(
                        f"{from_city}→{to_city} {dist}km/2h")
                    await repo.record_member_geo(member_id, ip)
                    return {"score": max(0.0, score),
                            "details": details, "hasSignal": True}
            # 回退粗口径: 记录当前可定位 IP + distinct 计数
            await repo.record_member_geo(member_id, ip)
            distinct = len({h for h in history if lookup_geo(h)})
            if distinct >= 2:
                score -= 50.0
                details.append(f"两小时窗口跨{distinct}地跳变")
                return {"score": score, "details": details,
                        "hasSignal": True}
        elif current:
            await repo.record_member_geo(member_id, ip)
    except Exception as exc:
        logger.warning("geo_velocity_skip member=%s: %s",
                       member_id, exc)
    return {"score": max(0.0, score), "details": details,
            "hasSignal": bool(details)}
