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
from typing import ClassVar

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
    # 情景引擎(时间×空间×用户×节日×天气 规则表)
    # ============================================================

    # 主要节日表(公历日期; 农历节日按年硬编码 2026-2027——
    # 问候文案级精度, 个别农历日差一天无业务影响)
    HOLIDAYS: ClassVar[dict[str, str]] = {
        # ---- 2026 ----
        "2026-01-01": "元旦",
        "2026-02-16": "除夕", "2026-02-17": "春节", "2026-02-18": "春节",
        "2026-02-19": "春节", "2026-02-20": "春节", "2026-02-21": "春节",
        "2026-02-22": "春节", "2026-02-23": "春节",
        "2026-03-03": "元宵节", "2026-03-04": "元宵节",
        "2026-04-05": "清明节", "2026-05-01": "劳动节", "2026-05-02": "劳动节",
        "2026-05-03": "劳动节", "2026-06-19": "端午节", "2026-06-20": "端午节",
        "2026-08-19": "七夕", "2026-09-25": "中秋节", "2026-09-26": "中秋节",
        "2026-10-01": "国庆节", "2026-10-02": "国庆节", "2026-10-03": "国庆节",
        "2026-10-04": "国庆节", "2026-10-05": "国庆节", "2026-10-06": "国庆节",
        "2026-10-07": "国庆节", "2026-10-18": "重阳节",
        "2026-11-11": "双十一", "2026-12-12": "双十二",
        # ---- 2027 ----
        "2027-01-01": "元旦",
        "2027-02-05": "除夕", "2027-02-06": "春节", "2027-02-07": "春节",
        "2027-02-08": "春节", "2027-02-09": "春节", "2027-02-10": "春节",
        "2027-02-11": "春节", "2027-02-12": "春节",
        "2027-02-20": "元宵节", "2027-02-21": "元宵节",
        "2027-04-05": "清明节", "2027-05-01": "劳动节", "2027-05-02": "劳动节",
        "2027-05-03": "劳动节", "2027-06-09": "端午节", "2027-06-10": "端午节",
        "2027-08-08": "七夕", "2027-09-15": "中秋节", "2027-09-16": "中秋节",
        "2027-10-01": "国庆节", "2027-10-02": "国庆节", "2027-10-03": "国庆节",
        "2027-10-04": "国庆节", "2027-10-05": "国庆节", "2027-10-06": "国庆节",
        "2027-10-07": "国庆节", "2027-10-08": "重阳节",
        "2027-11-11": "双十一", "2027-12-12": "双十二",
    }

    def build_greeting(self, location: dict, weather: dict,
                       is_member: bool, date: str = None) -> dict:
        """生成个性化问候(规则表: 节日>时段 × 城市 × 新老客 × 天气)

        Args:
            date: 日期注入(YYYY-MM-DD, 测试用; 缺省今天)
        Returns:
            {greeting, sub, weatherTip, period, festival, background}
            background: 动态背景码(festival/snowy/rainy/foggy/night/
            sunny/cloudy/day)——前端渐变主题切换
        """
        # 北京时间(UTC+8 固定偏移, 中国无夏令时)——时段与节日判定
        # 须用用户本地时钟: ts() 是 UTC, 直接取小时对中国用户错位 8h
        # (早 7 点被问候"夜深了"), 节日凌晨 0-8 点亦会按前一天漏判
        from datetime import UTC, datetime, timedelta

        now_cn = datetime.now(UTC) + timedelta(hours=8)
        today = date or now_cn.strftime("%Y-%m-%d")
        hour = now_cn.hour
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

        # 节日问候(优先于时段——节日情绪压倒日常)
        festival = self.HOLIDAYS.get(today) or ""
        greet_word = f"{festival}快乐" if festival else greet
        greeting = f"{greet_word}，欢迎{where}{who}！"

        # 天气行 + 天气关怀提示
        sub = ""
        if weather.get("available"):
            sub = (f"{city} · {weather.get('weather')} · "
                   f"{weather.get('temperature')}°C")
            if weather.get("humidity"):
                sub += f" · 湿度{weather['humidity']}%"
        weather_tip = self._weather_tip(city, weather)

        # 动态背景码
        background = self._background(period, weather, bool(festival))
        return {"greeting": greeting, "sub": sub, "weatherTip": weather_tip,
                "period": period, "festival": festival,
                "background": background}

    @staticmethod
    def _weather_tip(city: str, weather: dict) -> str:
        """天气关怀提示(雨雪/高温/严寒——设计稿情景规则)"""
        if not weather.get("available"):
            return ""
        text = weather.get("weather") or ""
        try:
            temp = float(weather.get("temperature") or "")
        except (TypeError, ValueError):
            temp = None
        if "雪" in text:
            return f"{city}今日有雪，注意保暖，宜温一壶竹香酒"
        if "雨" in text:
            return f"{city}今天有雨，出门备伞，宜在家小酌"
        if temp is not None and temp >= 35:
            return f"{city}高温预警，注意防暑降温，冰镇竹香更爽口"
        if temp is not None and temp <= 0:
            return f"{city}天寒地冻，注意保暖"
        return ""

    @staticmethod
    def _background(period: str, weather: dict, festival: bool) -> str:
        """动态背景码(节日>天气>昼夜>默认)"""
        if festival:
            return "festival"
        if weather.get("available"):
            text = weather.get("weather") or ""
            if "雪" in text:
                return "snowy"
            if "雨" in text:
                return "rainy"
            if "雾" in text or "霾" in text:
                return "foggy"
            if "晴" in text:
                return "sunny" if period != "night" else "night"
            if "云" in text or "阴" in text:
                return "cloudy"
        return "night" if period == "night" else "day"

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
