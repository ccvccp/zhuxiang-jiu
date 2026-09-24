"""小竹地图问话·map.nearby(智图 P0 × 小竹语音联动)

智图(zt)全域时空中枢的语音入口: 用户喊「附近哪有卖竹香酒的」
→ 智图 POI 观测面(list_pois, 只读) → 类型意图过滤(直营/
体验/餐饮) → 语音播报最近门店清单。

设计红线:
    - 智图 POI 数据层直调(service 层观测面)——决策面(POST)
      门控不受影响, ZT 三态任何档均可回答门店问题
    - 事实只来自智图 POI 库, 本模块不产数字
    - 无用户实时坐标(语音场景无浏览器 geolocation)——
      营业状态优先播报; ZtResourceService.nearby 的距离
      排序留待前端传坐标时启用(接口天然支持)
"""

# 直营卖酒(旗舰/体验/零售) vs 餐饮(边吃边买场景)
_SALE_POI_TYPES = ("flagship", "experience", "retail")
_DINING_POI_TYPES = ("dining",)


class XiaozhuMapService:
    """附近门店问话 → 智图 POI 清单播报(全只读)"""

    def _safe(self, reply: str) -> dict:
        return {"reply": reply, "card": None}

    async def nearby(self, text: str,
                     member_id: int | None = None) -> dict:
        """附近门店问话(类型意图: 吃饭场景→餐饮 POI;
        默认→竹香酒直营)。member_id 预留收货地址定位增强。"""
        t = str(text or "")
        from services.zt_resource_service import (
            ZtResourceService,
        )
        try:
            pois = await ZtResourceService().fabric.list_pois()
        except Exception:  # noqa: BLE001
            return self._safe(
                "门店信息暂时没取到, 请稍后再试。")
        if not pois:
            return self._safe(
                "门店地图还在筹备中, 可在线上下单同样送到家。")
        dining = any(w in t for w in ("吃", "餐", "饭", "宴"))
        types = (_DINING_POI_TYPES if dining
                 else _SALE_POI_TYPES)
        rows = [p for p in pois
                if p.get("poiType") in types]
        if not rows:
            rows = [p for p in pois if p.get("open")]
        rows.sort(key=lambda p: (not p.get("open"),
                                 str(p.get("poiCode"))))
        if not rows:
            return self._safe(
                "附近暂时没有营业中的门店, "
                "可在线上下单配送到家。")
        top = rows[:3]
        head = ("最近的用餐点" if dining
                else "最近的竹香酒门店")
        parts = []
        for p in top:
            st = "营业中" if p.get("open") else "未营业"
            parts.append(f"{p.get('name')}({st})")
        tail = ("" if dining
                else "; 也可在线上下单直接配送到家")
        reply = (f"{head}: " + "、".join(parts)
                 + f"。共{len(rows)}家可选{tail}。")
        return {"reply": reply, "card": None}
