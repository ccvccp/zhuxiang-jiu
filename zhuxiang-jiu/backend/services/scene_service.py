"""时空情景感知服务(P0)

三层职责:
    - 空间解析: ip2region 离线库 → 区划册命中 cityCode(344 地级市)
    - 天气感知: 高德天气 API(adcode 直查) + 双模式缓存 30min,
      未配置 Key 优雅降级(available=False)
    - 情景聚合: 一次请求返回 城市+天气+代理权益+问候语
      (前端情景条单接口聚合, 减少 RTT)

降级链(设计文档 2.1): IP 解析失败/海外段 → 空城市
(前端 fallback 浏览器 Geolocation 或手动切换城市——手动切换
经 city_code 参数覆盖 IP 定位)。

隐私口径: 城市级粒度; IP 原文仅瞬态解析不落库。
"""

import asyncio
import json
import logging
import time

from repositories.backend import (
    is_redis_mode, get_redis_client, _k)

logger = logging.getLogger(__name__)

# 天气缓存 TTL(秒)——天气变化频率低, 半小时足够
WEATHER_CACHE_TTL = 1800

# 高德天气 API(免费 Key: GAODE_WEATHER_KEY 环境变量注入,
# 未配置时 available=False 优雅降级——与短信通道同款回退模式)
_GAODE_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"

# 区划册城市名索引(模块级一次构建)
from services import citystore_regions

_CITY_NAME_INDEX: dict[str, dict] = {
    c["cityName"]: c for c in citystore_regions.all_cities()}

# 内存模式天气缓存(Redis 模式走 Redis)
_mem_cache: dict[str, tuple[float, dict]] = {}


class SceneService:
    """时空情景感知(城市解析/天气/代理/问候聚合)"""

    # ============================================================
    # 空间解析
    # ============================================================

    def resolve_city(self, ip: str) -> dict:
        """IP → 城市上下文(ip2region + 区划册命中 cityCode)

        Returns:
            {ip, country, province, city, cityCode, matched}
            matched=False 表示未命中区划册(海外/机房段/库缺失)——
            前端走降级链(Geolocation/手动切换)
        """
        from services import geoip

        region = geoip.parse_region(geoip.search(ip))
        city_name = region.get("city") or ""
        # 无城市(海外/未知)时以省名兜底尝试(如北京 IP 城市段可为省)
        ref = _CITY_NAME_INDEX.get(city_name)
        if ref is None and region.get("countryCode") == "CN":
            ref = _CITY_NAME_INDEX.get(region.get("province") or "")
        matched = ref is not None
        return {
            "ip": ip,
            "country": region.get("country") or "",
            "province": ref["provinceName"] if matched
            else (region.get("province") or ""),
            "city": ref["cityName"] if matched else city_name,
            "cityCode": ref["cityCode"] if matched else "",
            "matched": matched,
        }

    # ============================================================
    # 天气感知
    # ============================================================

    async def get_weather(self, city_code: str) -> dict:
        """查询城市实时天气(缓存 30min; 未配置高德 Key 优雅降级)

        Returns:
            {available, source, city, weather, temperature,
             humidity, wind, reportTime}
        """
        import os

        city_code = str(city_code or "")
        if not city_code:
            return {"available": False, "source": "none"}
        cached = await self._cache_get("weather", city_code)
        if cached is not None:
            return cached
        key = os.environ.get("GAODE_WEATHER_KEY") or ""
        if not key:
            # 降级: 未配置 Key(与短信模拟通道同款回退——配置即激活)
            result = {"available": False, "source": "unconfigured",
                      "cityCode": city_code}
        else:
            result = await self._fetch_gaode(key, city_code)
        await self._cache_set("weather", city_code, result)
        return result

    async def _fetch_gaode(self, key: str, city_code: str) -> dict:
        """高德天气 API 实时查询(to_thread 包装同步 urllib)"""
        import urllib.parse
        import urllib.request

        url = (_GAODE_WEATHER_URL + "?"
               + urllib.parse.urlencode(
                   {"city": city_code, "key": key}))
        try:
            def _get() -> dict:
                with urllib.request.urlopen(url, timeout=6) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            payload = await asyncio.to_thread(_get)
        except (OSError, ValueError) as exc:
            logger.warning("[情景天气] 高德查询失败 %s: %s", city_code, exc)
            return {"available": False, "source": "error",
                    "cityCode": city_code}
        lives = (payload or {}).get("lives") or []
        if (payload or {}).get("status") != "1" or not lives:
            logger.warning("[情景天气] 高德业务失败 %s: %s",
                           city_code, str(payload)[:200])
            return {"available": False, "source": "error",
                    "cityCode": city_code}
        live = lives[0]
        return {
            "available": True, "source": "gaode",
            "cityCode": city_code,
            "city": live.get("city") or "",
            "weather": live.get("weather") or "",
            "temperature": live.get("temperature") or "",
            "humidity": live.get("humidity") or "",
            "wind": ((live.get("winddirection") or "")
                     + (live.get("windpower") or "")),
            "reportTime": live.get("reporttime") or "",
        }

    # 双模式缓存(30min)

    async def _cache_get(self, ns: str, key: str) -> dict | None:
        if is_redis_mode():
            client = await get_redis_client()
            raw = await client.get(_k("scene", ns, key))
            return json.loads(raw) if raw else None
        hit = _mem_cache.get(f"{ns}:{key}")
        if hit and hit[0] > time.time():
            return hit[1]
        return None

    async def _cache_set(self, ns: str, key: str, value: dict) -> None:
        if is_redis_mode():
            client = await get_redis_client()
            await client.set(_k("scene", ns, key),
                             json.dumps(value, ensure_ascii=False),
                             ex=WEATHER_CACHE_TTL)
        else:
            _mem_cache[f"{ns}:{key}"] = (time.time() + WEATHER_CACHE_TTL,
                                         value)

    # ============================================================
    # 代理匹配(城市代理 = 市级网店体系)
    # ============================================================

    async def get_agent(self, city_code: str) -> dict | None:
        """查询城市代理(运营中市级网店; 无店返回 None 引导开店)

        城市代理权益即市级网店体系(一市一店/保证金/月度考核),
        零新建表——P1 订单归属同源。
        """
        from repositories.citystore_repository import CityStoreRepository

        if not city_code:
            return None
        store = await CityStoreRepository().get_by_city(str(city_code))
        if not store:
            return None
        return {
            "storeCode": store.get("storeCode"),
            "storeName": store.get("storeName"),
            "cityName": store.get("cityName"),
            "provinceName": store.get("provinceName"),
        }

    # ============================================================
    # 情景引擎(时间×空间×用户 规则表, P0 最小集)
    # ============================================================

    def build_greeting(self, location: dict, weather: dict,
                       is_member: bool) -> dict:
        """生成个性化问候(确定性规则——LLM 增强位预留 P2)"""
        from core.helpers import ts

        hour = int(ts()[11:13])
        if 5 <= hour < 9:
            period, greet = "morning", "早上好"
        elif 9 <= hour < 12:
            period, greet = "forenoon", "上午好"
        elif 12 <= hour < 14:
            period, greet = "noon", "中午好"
        elif 14 <= hour < 18:
            period, greet = "afternoon", "下午好"
        elif 18 <= hour < 23:
            period, greet = "evening", "晚上好"
        else:
            period, greet = "night", "夜深了"

        city = location.get("city") or ""
        province = location.get("province") or ""
        if location.get("matched") and city:
            where = f"来自{province}{city}的"
        elif province:
            where = f"来自{province}的"
        else:
            where = ""
        who = "老朋友" if is_member else "朋友"
        greeting = f"{greet}，欢迎{where}{who}！"

        # 天气行(高德中文天气文本直用)
        sub = ""
        if weather.get("available"):
            sub = (f"{city} · {weather.get('weather')} · "
                   f"{weather.get('temperature')}°C")
            if weather.get("humidity"):
                sub += f" · 湿度{weather['humidity']}%"
        return {"greeting": greeting, "sub": sub, "period": period}

    # ============================================================
    # 聚合
    # ============================================================

    async def get_context(self, ip: str, city_code: str = None,
                          member_id: int = None) -> dict:
        """情景聚合(手动切换城市优先; 一次返回四域)

        Returns:
            {location, weather, agent, greeting, policy}
        """
        # 手动切换优先(用户自主权——隐私合规要求)
        manual = str(city_code or "")
        if manual:
            ref = citystore_regions.get_city(manual)
            if ref:
                location = {
                    "ip": ip, "country": "中国",
                    "province": ref["provinceName"], "city": ref["cityName"],
                    "cityCode": ref["cityCode"], "matched": True,
                    "manual": True,
                }
            else:  # 非法码——回退 IP 解析
                location = self.resolve_city(ip)
        else:
            location = self.resolve_city(ip)

        code = location.get("cityCode") or ""
        weather = (await self.get_weather(code)
                   if code else {"available": False, "source": "none"})
        agent = await self.get_agent(code)
        is_member = bool(member_id)
        greeting = self.build_greeting(location, weather, is_member)
        return {
            "location": location,
            "weather": weather,
            "agent": agent,
            "greeting": greeting,
            # 隐私告知(前端页脚展示)
            "policy": "根据您的 IP 所在城市提供本地化服务(仅城市级, 不存储原始 IP)",
        }
