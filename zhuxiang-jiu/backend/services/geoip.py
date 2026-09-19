"""ip2region xdb 离线 IP 定位(纯标准库, 全内存单例)

数据文件: data/ip2region.xdb(v3 结构 IPv4 版, ~11MB, jsdelivr
lionsoul2014/ip2region data/ip2region_v4.xdb)——进程启动后首次
查询时全量载入内存, 后续微秒级二分查询。

xdb v3 结构(IPv4):
    Header(256B) + VectorIndex(256x256x8) + 索引段 + 数据段
    索引项 14B: start_ip(4, 小端) + end_ip(4, 小端)
              + data_len(2, 小端) + data_ptr(4, 小端)
    region 格式(v3 实测): 国家|省份|城市|ISP|国家码
    (如 中国|山东省|泰安市|联通|CN; 海外段城市/ISP 为 0)

参考: ip2region binding/python(Apache-2.0, 精简自包含化)
"""

import ipaddress
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_HEADER_LEN = 256
_VECTOR_INDEX_COLS = 256
_VECTOR_INDEX_SIZE = 8
_INDEX_SIZE = 14  # 4(start)+4(end)+2(len)+4(ptr)

_XDB_PATH = os.environ.get(
    "IP2REGION_XDB",
    str(Path(__file__).resolve().parent.parent / "data" / "ip2region.xdb"))

_buffer: bytes | None = None


def _load() -> bytes | None:
    """全量载入 xdb(成功后进程级单例; 失败不缓存——文件后挂载自愈)"""
    global _buffer
    if _buffer:
        return _buffer
    try:
        if not os.path.exists(_XDB_PATH):
            return None  # 库缺失: 不缓存失败, 下次请求重探(volume 后挂自愈)
        with open(_XDB_PATH, "rb") as f:
            _buffer = f.read()
        logger.info("ip2region_loaded path=%s size=%d",
                    _XDB_PATH, len(_buffer))
        return _buffer
    except OSError as exc:
        logger.warning("ip2region_load_failed path=%s: %s(降级: 无城市)",
                       _XDB_PATH, exc)
        return None


def _le32(buff: bytes, offset: int) -> int:
    return (buff[offset] | (buff[offset + 1] << 8)
            | (buff[offset + 2] << 16) | (buff[offset + 3] << 24))


def _le16(buff: bytes, offset: int) -> int:
    return buff[offset] | (buff[offset + 1] << 8)


def _cmp_be_vs_le(ip: bytes, buff: bytes, offset: int) -> int:
    """大端输入 IP 与 xdb 小端索引比较(逆序字节)"""
    j = offset + len(ip) - 1
    for i in range(len(ip)):
        if ip[i] < buff[j]:
            return -1
        if ip[i] > buff[j]:
            return 1
        j -= 1
    return 0


def search(ip: str) -> str:
    """查询 IP 归属(国家|区域|省份|城市|ISP)

    Returns:
        region 字符串; 无法解析/未命中/库缺失返回 ""
    """
    buf = _load()
    if buf is None:
        return ""
    try:
        b = ipaddress.ip_address(str(ip).strip()).packed
    except ValueError:
        return ""
    if len(b) != 4:  # v4 库不支持 IPv6
        return ""
    # 向量索引定位索引段范围
    idx = (b[0] * _VECTOR_INDEX_COLS + b[1]) * _VECTOR_INDEX_SIZE
    s_ptr = _le32(buf, _HEADER_LEN + idx)
    e_ptr = _le32(buf, _HEADER_LEN + idx + 4)
    if s_ptr == 0 or e_ptr == 0:
        return ""
    # 索引段二分(start/end 双边界)
    lo, hi = 0, (e_ptr - s_ptr) // _INDEX_SIZE
    while lo <= hi:
        mid = (lo + hi) >> 1
        p = s_ptr + mid * _INDEX_SIZE
        if _cmp_be_vs_le(b, buf, p) < 0:
            hi = mid - 1
        elif _cmp_be_vs_le(b, buf, p + 4) > 0:
            lo = mid + 1
        else:
            d_len = _le16(buf, p + 8)
            d_ptr = _le32(buf, p + 10)
            return buf[d_ptr:d_ptr + d_len].decode("utf-8")
    return ""


def parse_region(region: str) -> dict:
    """region 字符串解析为结构(v3 实测格式: 国家|省份|城市|ISP|国家码)

    city/isp 为 "0" 时归一化为空串(海外段/未知)。
    """
    parts = (region or "").split("|")
    parts += [""] * (5 - len(parts))
    country, province, city, isp, country_code = parts[:5]

    def _norm(v: str) -> str:
        return "" if v in ("0", "-") else v

    return {"country": _norm(country), "province": _norm(province),
            "city": _norm(city), "isp": _norm(isp),
            "countryCode": _norm(country_code)}
