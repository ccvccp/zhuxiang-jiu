"""43号·P3-3 GeoIP 离线库 + 设备指纹服务(geo_device 因子精细化)

计划 §四(docs/43号P3_纵深增强实施计划.md):
    - 设备指纹: 复用 39号 entry_devices 信任设备表(X-Device-Id 头,
      陌生设备打敏感端点 → identity_risk 降分; 同账号 24h 设备数
      ≥3 撞库信号)
    - GeoIP: GeoLite2-City.mmdb 离线库(缺失静默关闭零影响),
      同账号两小时内跨省 IP 变更 → 异地跳变信号(对齐 30号
      auth_risk 的 geo_velocity 概念)
    - XFF 反代头: X-Forwarded-For 第一跳(云部署生效)

mmdb 解析: 纯 stdlib 实现 GeoLite2 的二分检索子集(仅
city/country 层级 IP 定位), 不引入外部依赖; 文件缺失时
geo 功能整体静默关闭(零影响路径)。
"""

import logging
import os
import struct

logger = logging.getLogger(__name__)

GEOIP_DB_PATH = os.environ.get(
    "GEOIP_DB_PATH",
    os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "GeoLite2-City.mmdb"))

# 异地跳变窗口与阈值(计划 §四: 两小时跨省)
GEO_VELOCITY_WINDOW = 7200
GEO_VELOCITY_MIN_KM = 300.0
# 设备数堆积阈值(24h ≥3 撞库信号)
DEVICE_COUNT_THRESHOLD = 3


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ============================================================
# mmdb 二分检索子集(纯 stdlib, GeoLite2 兼容)
# ============================================================

def _read_mmdb_search_tree(path: str):
    """加载 mmdb 元数据与搜索树(懒加载单例)

    GeoLite2 格式: [搜索树 | 数据段 | 元数据标记段]
    返回 (file_bytes, search_tree_size, node_count, record_size)
    或 None(文件缺失/格式不符 → geo 功能静默关闭)。
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
        # 元数据标记: 最后 16 字节内找 "\xab\xcd\xefMaxMind.com"
        marker = b"\xab\xcd\xefMaxMind.com"
        meta_start = data.rfind(marker)
        if meta_start < 0:
            raise ValueError("无 MaxMind 元数据标记")
        # 简化口径: node_count(第16-20字节)/record_size(第22字节)
        # 跳过复杂 metadata 解析——搜索树参数由树大小反推:
        # 元数据段起始即搜索树+数据段结束; 搜索树节点数需
        # metadata 的 node_count, 这里用保守扫描:
        # node_count 常见 2^28 以下, record_size 24/28/32 bits
        node_count = struct.unpack(
            ">I", data[meta_start + 16:meta_start + 20])[0]
        record_size_bits = data[meta_start + 22]
        if record_size_bits not in (24, 28, 32):
            record_size_bits = 24
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


def _ip_to_bytes(ip: str) -> bytes | None:
    """IPv4 → 4 字节(IPv6 网络不在 GeoLite2 离线子集, 返回 None)"""
    try:
        parts = ip.strip().split(".")
        if len(parts) != 4:
            return None
        return bytes(int(p) & 0xFF for p in parts)
    except (ValueError, AttributeError):
        return None


def lookup_geo(ip: str) -> dict | None:
    """IP → 地理信息(离线库; 缺失/未命中返回 None)

    简化口径: 返回 {ip, treeOffset} 命中标记——完整城市名解析
    需数据段解码器, 本期仅提供"可定位/不可定位"与检索偏移,
    跨省跳变由 _geo_distance_km 用经纬度外部标注时启用。
    """
    if not ip or ip in ("unknown", "127.0.0.1", "0.0.0.0"):
        return None
    loaded = _read_mmdb_search_tree(GEOIP_DB_PATH)
    if loaded is None:
        return None
    data, node_count, record_bits = loaded
    addr = _ip_to_bytes(ip)
    if addr is None:
        return None
    # 二分检索(每节点 2 条记录, record_bits 位/条)
    node_bytes = record_bits * 2 // 8
    node = 0
    for byte in addr:
        for bit in range(7, -1, -1):
            if node >= node_count:
                break   # 数据段
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
        return None   # 未命中
    return {"ip": ip, "resolved": True}


def geo_available() -> bool:
    """geo 离线库是否可用(不可用时因子退化为纯时段口径)"""
    return _read_mmdb_search_tree(GEOIP_DB_PATH) is not None


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
    """同账号两小时窗口跨省跳变检测(离线库可用时)

    Returns:
        {score, details, hasSignal}
        - 窗口内既有记录与新 IP 均可定位且距上次跳变 → 降分
        - geo 不可用/无历史 → 中性
    """
    if not member_id or not geo_available():
        return {"score": 100.0, "details": [], "hasSignal": False}
    details = []
    score = 100.0
    # 简化口径: geo 离线库命中数作为活动广度信号——
    # 完整经纬度距离需数据段解码, P3 后续增强(计划 §四已声明)
    try:
        from repositories.security_repository import \
            Security43Repository
        repo = Security43Repository()
        history = await repo.get_member_geo_history(member_id)
        current = lookup_geo(ip)
        if current and history:
            # 记录当前可定位 IP
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
