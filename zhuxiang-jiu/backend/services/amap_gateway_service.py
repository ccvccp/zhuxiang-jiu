"""高德地图 LBS 网关(P2 批次②·Provider 三态灰度)

模式(LOC_AMAP_MODE, 对齐 PAY60_CHANNEL_MODE 范式):
    mock(默认)      全走 MockAmapGateway, 确定性回执(按地址 md5 生成
                    坐标/adcode), 零凭证本地/CI 可测
    mock_fallback   先走真实高德 Web 服务 API, 失败/超时自动降级 mock
                    并落 fallback/fallbackReason 留痕(不阻断业务)
    real            全走真实渠道; 凭证(AMAP_KEY, 可选 AMAP_SECRET 数字
                    签名)缺失 fail-hard(ValueError)

用到的接口(real 轨):
    地理编码   GET /v3/geocode/geocode
    逆地理     GET /v3/geocode/regeo
    驾车距离   GET /v3/distance?type=1

观测: real 调用留 gatewayCall 痕(耗时/结果/降级原因), 复用 logger。
"""
import hashlib
import os
import time
from urllib.parse import quote

AMAP_BASE = "https://restapi.amap.com"
# Mock 确定性坐标域(设计稿 §1.3): [117°~120°E, 34°~37°N]
MOCK_LNG_RANGE = (117.0, 120.0)
MOCK_LAT_RANGE = (34.0, 37.0)

import logging

logger = logging.getLogger("services.amap_gateway_service")


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _mode() -> str:
    m = (_env("LOC_AMAP_MODE") or "mock").lower()
    return m if m in ("mock", "mock_fallback", "real") else "mock"


def _md5_int(text: str, salt: str = "") -> int:
    return int(hashlib.md5(
        f"{salt}:{text}".encode("utf-8")).hexdigest()[:12], 16)


class AmapGateway:
    """高德 Web 服务 API(real 轨; Web 服务 Key + 可选数字签名)"""

    REQUIRED = ("AMAP_KEY",)

    def __init__(self, http_get=None):
        self._http_get = http_get

    def _credentials(self) -> dict:
        missing = [k for k in self.REQUIRED if not _env(k)]
        if missing:
            raise ValueError(
                f"高德凭证未配置({','.join(missing)}), "
                f"经 .env 注入——real 渠道 fail-hard 不静默降级")
        return {"key": _env("AMAP_KEY"),
                "secret": _env("AMAP_SECRET")}

    def _signed_query(self, path: str, params: dict) -> str:
        """构造签名 query(数字签名可选: sig=md5(path+k1=v1&..&secret))

        无 AMAP_SECRET 时仅 key 明文(高德支持, 建议 IP 白名单加固)。
        """
        creds = self._credentials()
        query = dict(params, key=creds["key"])
        plain = "&".join(
            f"{k}={query[k]}" for k in sorted(query)
            if query[k] not in ("", None))
        if creds["secret"]:
            sig = hashlib.md5(
                f"{path}?{plain}{creds['secret']}".encode("utf-8")
            ).hexdigest()
            plain = f"{plain}&sig={sig}"
        return plain

    async def _get(self, path: str, params: dict) -> dict:
        """GET + 3s 超时 + 2 次重试(高德限频建议)"""
        url = f"{AMAP_BASE}{path}?{self._signed_query(path, params)}"
        last_err = None
        for attempt in range(3):
            started = time.time()
            try:
                status, resp = await self._http_get(url)
                elapsed = round((time.time() - started) * 1000, 1)
                resp = resp if isinstance(resp, dict) else {}
                logger.info(
                    "amap_call path=%s attempt=%s status=%s "
                    "elapsed_ms=%s", path, attempt + 1, status, elapsed)
                if status == 200 and str(resp.get("status")) == "1":
                    return resp
                last_err = ValueError(
                    f"高德接口拒绝(HTTP {status}, "
                    f"infocode={resp.get('infocode')}, "
                    f"info={resp.get('info')})")
            except Exception as exc:   # 网络/超时
                last_err = exc
                logger.warning("amap_call_failed path=%s err=%s",
                               path, exc)
            time.sleep(0.2 * (attempt + 1))   # 退避后重试
        raise last_err or ValueError("高德接口调用失败")

    async def geocode(self, address: str, city: str = "") -> dict:
        resp = await self._get("/v3/geocode/geocode", {
            "address": address, "city": city or ""})
        geos = resp.get("geocodes") or []
        if not geos:
            raise ValueError(f"高德地理编码无结果(address={address})")
        loc = str(geos[0].get("location") or "")
        lng, _, lat = loc.partition(",")
        return {
            "longitude": float(lng) if lng else None,
            "latitude": float(lat) if lat else None,
            "adcode": str(geos[0].get("adcode") or "") or None,
            "formatted": geos[0].get("formatted_address") or "",
            "level": geos[0].get("level") or "",
        }

    async def regeo(self, longitude: float, latitude: float) -> dict:
        resp = await self._get("/v3/geocode/regeo", {
            "location": f"{longitude},{latitude}"})
        addr = ((resp.get("regeocode") or {}).get("addressComponent")
                or {})
        return {
            "adcode": str(addr.get("adcode") or "").strip("[]") or None,
            "city": str(addr.get("city") or "").strip("[]") or None,
            "province": str(addr.get("province") or "").strip("[]")
            or None,
            "formatted": (resp.get("regeocode") or {})
            .get("formatted_address") or "",
        }

    async def driving_distance(self, origins: str,
                               destination: str) -> dict:
        """驾车路网距离(origins/destination 均为 'lng,lat')"""
        resp = await self._get("/v3/distance", {
            "origins": origins, "destination": destination,
            "type": "1"})
        results = resp.get("results") or []
        if not results:
            raise ValueError("高德距离接口无结果")
        return {
            "distanceKm": round(
                int(results[0].get("distance") or 0) / 1000.0, 2),
            "durationMin": round(
                int(results[0].get("duration") or 0) / 60.0, 1),
        }


class MockAmapGateway:
    """模拟通道(确定性: 按地址 md5 生成坐标/adcode, 可复现可断言)"""

    async def geocode(self, address: str, city: str = "") -> dict:
        h = _md5_int(address, "geo")
        lng = round(MOCK_LNG_RANGE[0]
                    + (h % 3000) / 1000.0, 6)
        lat = round(MOCK_LAT_RANGE[0]
                    + ((h >> 12) % 3000) / 1000.0, 6)
        adcode = f"{(h >> 24) % 100000:05d}"
        return {
            "longitude": lng,
            "latitude": lat,
            "adcode": adcode,
            "formatted": f"MOCK::{city or ''}{address}",
            "level": "mock",
        }

    async def regeo(self, longitude: float, latitude: float) -> dict:
        h = _md5_int(f"{longitude:.4f},{latitude:.4f}", "regeo")
        return {
            "adcode": f"{h % 100000:05d}",
            "city": "mock市",
            "province": "mock省",
            "formatted": f"MOCK省mock市({longitude:.4f},"
                         f"{latitude:.4f})",
        }

    async def driving_distance(self, origins: str,
                               destination: str) -> dict:
        # 路网系数 1.28(确定性): 由直线距离折算, 可稳定断言
        from repositories.location_repository import haversine_km
        o_lng, o_lat = map(float, origins.split(","))
        d_lng, d_lat = map(float, destination.split(","))
        straight = haversine_km(o_lng, o_lat, d_lng, d_lat)
        return {
            "distanceKm": round(straight * 1.28, 2),
            "durationMin": round(straight * 1.28 / 40.0 * 60.0, 1),
        }


class AmapGatewayService:
    """LBS 网关分发器(LOC_AMAP_MODE 三态)"""

    def __init__(self, http_get=None):
        self._real = AmapGateway(http_get)
        self._mock = MockAmapGateway()

    async def _execute(self, op: str, *args, **kwargs) -> dict:
        """三态分发: real 失败且 mock_fallback → 降级 mock 留痕"""
        mode = _mode()
        if mode == "mock":
            return await getattr(self._mock, op)(*args, **kwargs)
        try:
            result = await getattr(self._real, op)(*args, **kwargs)
            result["source"] = "amap"
            return result
        except Exception as exc:
            if mode == "mock_fallback":
                logger.warning(
                    "amap_fallback op=%s reason=%s", op, exc)
                result = await getattr(self._mock, op)(
                    *args, **kwargs)
                result["source"] = "mock"
                result["fallback"] = True
                result["fallbackReason"] = str(exc)[:200]
                return result
            raise   # real: fail-hard

    async def geocode(self, address: str, city: str = "") -> dict:
        return await self._execute("geocode", address, city)

    async def regeo(self, longitude: float, latitude: float) -> dict:
        return await self._execute("regeo", longitude, latitude)

    async def driving_distance(self, origins: str,
                               destination: str) -> dict:
        return await self._execute("driving_distance", origins,
                                   destination)
