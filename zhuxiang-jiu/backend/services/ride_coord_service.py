"""41号·坐标系转换工具(WGS84 ↔ 高德 GCJ-02, 凭证前置开发)

背景: 滴滴代驾接口坐标为高德系 GCJ-02, 本站起终点/种子司机为
WGS84 口径——直发真实平台前必须转换(否则派单定位偏移 50-500m,
甚至错城)。

设计:
    - 纯函数, 标准国测局算法(非线性偏移), 精度 ~1-2m
    - wgs84_to_gcj02 / gcj02_to_wgs84(逆转换, 二分逼近) 双向
    - out_of_china 判定(境外不偏移)
    - 环境开关 DRIDE_COORD_SYS: wgs84(默认, 本站内部口径不变)
      / gcj02(真实平台启用)——默认零影响, 凭证到手后一键切换
"""

import logging
import math
import os


logger = logging.getLogger(__name__)

# 输出坐标系开关: wgs84=本站内部口径(默认, 零影响)
#               gcj02=真实平台高德系(直发凭证到手后切换)
DRIDE_COORD_SYS = os.environ.get("DRIDE_COORD_SYS", "wgs84")

# 国测局偏移参数(公开算法常数)
_A = 6378245.0
_EE = 0.00669342162296594323


def out_of_china(lng: float, lat: float) -> bool:
    """境外判定(境外无 GCJ-02 偏移)"""
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(lng: float, lat: float) -> float:
    ret = (-100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat
           + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi)
            + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi)
            + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi)
            + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = (300.0 + lng + 2.0 * lat + 0.1 * lng * lng
           + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi)
            + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi)
            + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi)
            + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """WGS84 → GCJ-02(高德/腾讯系)

    Returns:
        (gcj_lng, gcj_lat); 境外坐标原样返回
    """
    lng, lat = float(lng), float(lat)
    if out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE))
                             / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat)
                             * math.pi)
    return round(lng + dlng, 6), round(lat + dlat, 6)


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02 → WGS84(逆转换, 单次迭代逼近 ~1m 精度)"""
    lng, lat = float(lng), float(lat)
    if out_of_china(lng, lat):
        return lng, lat
    glng, glat = wgs84_to_gcj02(lng, lat)
    return round(lng * 2 - glng, 6), round(lat * 2 - glat, 6)


def to_partner_coords(lng: float, lat: float) -> tuple[float, float]:
    """按输出坐标系开关转换经纬度(直发平台请求专用)

    DRIDE_COORD_SYS=gcj02 → WGS84 转 GCJ-02(真实平台);
    wgs84(默认) → 原样返回(本站内部口径, 零影响)。
    """
    if DRIDE_COORD_SYS == "gcj02":
        return wgs84_to_gcj02(lng, lat)
    return float(lng), float(lat)


def coord_sys() -> str:
    """当前输出坐标系(供日志/回执留痕)"""
    return DRIDE_COORD_SYS
