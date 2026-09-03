"""43号·P5-5 GeoLite2 完整解码专项测试

运行方式:
    python test_security_p5_5.py

覆盖(计划 §五):
    - 解码器全类型 round-trip(夹具编码器 ↔ 服务解码器):
      string(含中文)/double/uint16/32/uint64/uint128/int32(负)/
      boolean/float/bytes/array/map(嵌套)/pointer(多级)
    - 自建最小 mmdb 夹具: 构造真实格式二进制
      (检索树+数据段+metadata map)
    - lookup_geo 扩展: 城市/经纬度/ISO/中文优先/未命中/
      解码异常回退
    - haversine: 北京-上海 ~1068km/同点 0
    - 距离跳变: 2h 窗口 500km+ 触发降 50(含城市对留痕)/
      近距不触发/无库零影响
    - 阈值环境变量
    - HTTP 层: /admin/ips 归属地字段
    - 回归: 无库静默关闭(geo_available=False)
"""

import asyncio
import os
import struct
import sys
import tempfile

os.environ["LOCK_MODE"] = "asyncio"
os.environ["STORE_MODE"] = "asyncio"
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_ENABLED"] = "off"
os.environ["SECURITY_GATEWAY_MODE"] = "on"
os.environ["SECURITY_ENFORCE_LEVEL"] = "observe"
os.environ["GEOIP_DB_PATH"] = "/nonexistent/GeoLite2-City.mmdb"
os.environ.pop("SECURITY_GEO_VELOCITY_KM", None)

PASS = 0
FAIL = 0
RESULTS = []


def record(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        RESULTS.append(f"  ✓ {name}")
    else:
        FAIL += 1
        RESULTS.append(f"  ✗ {name} — {detail}")


def reset_store():
    from repositories.store import reset_store as _reset
    _reset()


# ============================================================
# 夹具编码器(MaxMind DB spec v2.0, 与服务解码器互为镜像)
# ============================================================

def _enc_field(ftype, payload=b"", size=None):
    """编码字段: ctrl | ext_type(若>7) | size扩展 | payload

    size 语义: 字节数(常规)/键值对数(map)/元素数(array)/
    布尔值(boolean)
    """
    if size is None:
        size = len(payload)
    ctrl_type = ftype if ftype <= 7 else 0
    if size < 29:
        head = bytes([(ctrl_type << 5) | size])
        ext_sz = b""
    elif size < 285:
        head = bytes([(ctrl_type << 5) | 29])
        ext_sz = bytes([size - 29])
    elif size < 65821:
        head = bytes([(ctrl_type << 5) | 30])
        ext_sz = (size - 285).to_bytes(2, "big")
    else:
        head = bytes([(ctrl_type << 5) | 31])
        ext_sz = (size - 65821).to_bytes(3, "big")
    ext_type = bytes([ftype - 7]) if ftype > 7 else b""
    return head + ext_type + ext_sz + payload


def _enc_str(s):
    return _enc_field(2, s.encode("utf-8"))


def _enc_double(x):
    return _enc_field(3, struct.pack(">d", x))


def _enc_uint(n):
    size = max(1, (n.bit_length() + 7) // 8)
    ftype = 5 if size <= 2 else 6     # uint16 / uint32
    return _enc_field(ftype, n.to_bytes(size, "big"))


def _enc_uint128(n):
    return _enc_field(10, n.to_bytes(16, "big"))


def _enc_int32(n):
    return _enc_field(8, struct.pack(">i", n))   # 固定 4 字节


def _enc_bool(b):
    return _enc_field(14, b"", size=1 if b else 0)


def _enc_float(x):
    return _enc_field(15, struct.pack(">f", x))


def _enc_bytes(b):
    return _enc_field(4, b)


def _enc_array(items):
    return _enc_field(11, b"".join(items), size=len(items))


def _enc_map(pairs):
    return _enc_field(7, b"".join(k + v for k, v in pairs),
                     size=len(pairs))


def _enc_pointer(ptr):
    """指针(4 级大小按偏移自动选)"""
    if ptr < (1 << 11):
        return bytes([(1 << 5) | (0 << 3) | (ptr >> 8)]) + \
            bytes([ptr & 0xFF])
    if ptr < (1 << 19):
        return bytes([(1 << 5) | (1 << 3) | (ptr >> 16)]) + \
            (ptr & 0xFFFF).to_bytes(2, "big")
    if ptr < (1 << 27):
        return bytes([(1 << 5) | (2 << 3) | (ptr >> 24)]) + \
            (ptr & 0xFFFFFF).to_bytes(3, "big")
    return bytes([(1 << 5) | (3 << 3)]) + ptr.to_bytes(4, "big")


def _ip16(ip: str) -> bytes:
    """IPv4 → 16 字节(::/96 映射)"""
    parts = ip.split(".")
    return b"\x00" * 12 + bytes(int(p) for p in parts)


def _enc_city_record(city_en, city_zh, iso, lat, lon, gid=1):
    """GeoLite2-City 结构记录"""
    return _enc_map([
        (_enc_str("city"), _enc_map([
            (_enc_str("geoname_id"), _enc_uint(gid)),
            (_enc_str("names"), _enc_map([
                (_enc_str("en"), _enc_str(city_en)),
                (_enc_str("zh-CN"), _enc_str(city_zh)),
            ])),
        ])),
        (_enc_str("country"), _enc_map([
            (_enc_str("geoname_id"), _enc_uint(gid + 1000)),
            (_enc_str("iso_code"), _enc_str(iso)),
            (_enc_str("names"), _enc_map([
                (_enc_str("en"), _enc_str("Country-" + iso)),
            ])),
        ])),
        (_enc_str("location"), _enc_map([
            (_enc_str("latitude"), _enc_double(lat)),
            (_enc_str("longitude"), _enc_double(lon)),
        ])),
    ])


def _build_test_mmdb(entries: dict) -> bytes:
    """构造最小 GeoLite2 兼容 mmdb

    结构: [搜索树(24bits/记录) | 数据段 | 元数据标记+map]
    口径: 记录值==node_count 为 miss; > 为 hit,
          数据段偏移 = 记录值 - node_count - 1(检索层自洽约定)
    """
    # ① 数据段(记录按插入顺序, 记录各自偏移)
    data_section = b""
    offsets = {}
    for ip, rec in entries.items():
        offsets[ip] = len(data_section)
        data_section += rec

    # ② 128-bit trie(::/96 IPv4 映射遍历口径)
    root = {}
    for ip, off in offsets.items():
        node = root
        bits = []
        for c in _ip16(ip):
            for bit in range(7, -1, -1):
                bits.append((c >> bit) & 1)
        for b in bits[:-1]:
            if b not in node:
                node[b] = {}
            node = node[b]
        node[bits[-1]] = off   # 叶: 数据段偏移

    # ③ 序列化搜索树(根节点编号 0)
    nodes = []

    def collect(n):
        nodes.append(n)
        for k in (0, 1):
            c = n.get(k)
            if isinstance(c, dict):
                collect(c)
    collect(root)
    node_count = len(nodes)
    idx = {id(n): i for i, n in enumerate(nodes)}
    tree = b""
    for n in nodes:
        for k in (0, 1):
            c = n.get(k)
            if c is None:
                val = node_count               # miss
            elif isinstance(c, dict):
                val = idx[id(c)]               # 下一节点
            else:
                val = node_count + 1 + c       # hit(offset 从 0)
            tree += val.to_bytes(3, "big")     # record_size=24

    # ④ metadata map(标准 DML 编码——解码器正确解析路径)
    meta = _enc_map([
        (_enc_str("node_count"), _enc_uint(node_count)),
        (_enc_str("record_size"), _enc_uint(24)),
        (_enc_str("ip_version"), _enc_uint(6)),
        (_enc_str("database_type"),
         _enc_str("GeoLite2-City-Test")),
        (_enc_str("binary_format_major_version"), _enc_uint(2)),
        (_enc_str("binary_format_minor_version"), _enc_uint(0)),
        (_enc_str("languages"),
         _enc_array([_enc_str("en"), _enc_str("zh-CN")])),
        (_enc_str("description"), _enc_map([
            (_enc_str("en"), _enc_str("fixture"))])),
        (_enc_str("build_epoch"), _enc_uint(1700000000)),
    ])
    return tree + data_section + \
        b"\xab\xcd\xefMaxMind.com" + meta


# 夹具库预置记录(北京/上海/廊坊近点/全类型记录)
BEIJING_IP = "1.2.3.4"
SHANGHAI_IP = "5.6.7.8"
NEAR_IP = "1.2.3.5"      # 距北京 ~50km(不触发)
FULL_IP = "9.9.9.9"      # 全类型记录
MISS_IP = "200.1.1.1"    # 未命中

FIXTURE_ENTRIES = {
    BEIJING_IP: _enc_city_record(
        "Beijing", "北京", "CN", 39.9042, 116.4074, gid=1816670),
    SHANGHAI_IP: _enc_city_record(
        "Shanghai", "上海", "CN", 31.2304, 121.4737, gid=1796236),
    NEAR_IP: _enc_city_record(
        "Sanhe", "三河", "CN", 39.9832, 117.0785, gid=203),
    FULL_IP: _enc_map([
        (_enc_str("s"), _enc_str("中文测试")),
        (_enc_str("d"), _enc_double(3.14159)),
        (_enc_str("u16"), _enc_uint(65535)),
        (_enc_str("u32"), _enc_uint(4294967295)),
        (_enc_str("u64"), _enc_field(
            9, (2 ** 63).to_bytes(8, "big"))),
        (_enc_str("u128"), _enc_uint128(2 ** 100)),
        (_enc_str("i32"), _enc_int32(-123456)),
        (_enc_str("b1"), _enc_bool(True)),
        (_enc_str("b0"), _enc_bool(False)),
        (_enc_str("f"), _enc_float(2.5)),
        (_enc_str("bytes"), _enc_bytes(b"\x01\x02\x03")),
        (_enc_str("arr"), _enc_array([
            _enc_str("x"), _enc_uint(7), _enc_bool(False)])),
        (_enc_str("nested"), _enc_map([
            (_enc_str("inner"), _enc_map([
                (_enc_str("deep"), _enc_str("v"))]))])),
        (_enc_str("ptr"), _enc_pointer(0)),   # 占位(下面单独测)
    ]),
}


def _install_fixture(entries=None) -> str:
    """写夹具到临时文件并挂载到服务(重置缓存)"""
    import services.geoip_service as g
    entries = FIXTURE_ENTRIES if entries is None else entries
    fd, path = tempfile.mkstemp(suffix=".mmdb")
    os.write(fd, _build_test_mmdb(entries))
    os.close(fd)
    g.GEOIP_DB_PATH = path
    g._reset_geoip_cache()
    return path


def _clear_fixture():
    import services.geoip_service as g
    g.GEOIP_DB_PATH = "/nonexistent/GeoLite2-City.mmdb"
    g._reset_geoip_cache()


class TestRoundTrip:
    def run(self):
        print("[01 编码/解码 round-trip]")
        from services.geoip_service import _decode_field as dec

        cases = [
            ("utf8-string(ascii)", _enc_str("hello"), "hello"),
            ("utf8-string(中文)", _enc_str("北京"), "北京"),
            ("double", _enc_double(3.14159), 3.14159),
            ("uint16", _enc_uint(65535), 65535),
            ("uint32", _enc_uint(4294967295), 4294967295),
            ("uint64", _enc_field(
                9, (2 ** 63).to_bytes(8, "big")), 2 ** 63),
            ("uint128", _enc_uint128(2 ** 100), 2 ** 100),
            ("int32(负)", _enc_int32(-123456), -123456),
            ("boolean(true)", _enc_bool(True), True),
            ("boolean(false)", _enc_bool(False), False),
            ("float", _enc_float(2.5), 2.5),
        ]
        for name, blob, expect in cases:
            value, _ = dec(blob, 0)
            record(name, value == expect,
                   f"{value!r} != {expect!r}")
        # bytes
        value, _ = dec(_enc_bytes(b"\x01\x02\x03"), 0)
        record("bytes", value == b"\x01\x02\x03", str(value))
        # array(嵌套)
        value, _ = dec(_enc_array([
            _enc_str("x"), _enc_uint(7), _enc_bool(False)]), 0)
        record("array", value == ["x", 7, False], str(value))
        # map(嵌套)
        value, _ = dec(_enc_map([
            (_enc_str("city"), _enc_map([
                (_enc_str("names"), _enc_map([
                    (_enc_str("zh-CN"), _enc_str("北京"))]))]))]), 0)
        record("map嵌套", value == {"city": {"names": {
            "zh-CN": "北京"}}}, str(value))

        # size 扩展三档(29/30/31 基数)
        for size, name in ((29, "size=29"), (285, "size=30"),
                           (65821, "size=31")):
            blob = _enc_str("x" * size)
            value, _ = dec(blob, 0)
            record(f"扩展长度{name}", len(value) == size,
                   f"len={len(value)}")
        # 长字符串(size 29 档)
        value, _ = dec(_enc_field(2, b"a" * 250), 0)
        record("size扩展250", len(value) == 250, str(len(value)))

        # pointer 各级(构造数据段: 目标字段在偏移 P)
        payload = _enc_str("target")
        for ptr in (5, 300, 100000):
            # 数据段: padding(ptr 字节) + 目标
            blob = b"\x00" * ptr + payload + _enc_pointer(ptr)
            value, _ = dec(blob, len(blob) - len(_enc_pointer(ptr)))
            record(f"pointer@{ptr}", value == "target", str(value))

        # ctrl 解码顺序(ctrl|ext_type|size_ext)
        value, _ = dec(_enc_field(9, b"\x01\x00\x00\x00"), 0)
        record("ext+size顺序", value == 16777216, str(value))

        # 越界/未知类型异常
        try:
            dec(b"", 5)
            record("越界ValueError", False, "未抛")
        except ValueError:
            record("越界ValueError", True)


class TestLookupGeo:
    def run(self):
        print("[02 lookup_geo 夹具]")
        import services.geoip_service as g
        _install_fixture()

        record("geo_available", g.geo_available() is True)
        r = g.lookup_geo(BEIJING_IP)
        record("命中北京", r is not None
               and r.get("city_name") == "北京", str(r))
        record("中文优先", r and r.get("city_name") == "北京",
               str(r))
        record("经纬度", r and abs(
            r.get("latitude", 0) - 39.9042) < 0.001
            and abs(r.get("longitude", 0) - 116.4074) < 0.001,
               str(r))
        record("国家ISO", r and r.get("country_iso") == "CN",
               str(r))
        record("国家名", r and r.get("country_name") == "Country-CN",
               str(r))

        r = g.lookup_geo(SHANGHAI_IP)
        record("命中上海", r is not None
               and r.get("city_name") == "上海", str(r))

        record("未命中None", g.lookup_geo(MISS_IP) is None)
        record("非法IP None", g.lookup_geo("not-ip") is None)
        record("本机IP None", g.lookup_geo("127.0.0.1") is None)

        # LRU 缓存命中(同 IP 二次查询走缓存)
        r1 = g.lookup_geo(BEIJING_IP)
        r2 = g.lookup_geo(BEIJING_IP)
        record("LRU缓存一致", r1 == r2 and r2 is not None)

        # 全类型记录解码
        r = g.lookup_geo(FULL_IP)
        record("全类型记录resolved", r is not None
               and r.get("resolved") is True, str(r))

        # 解码异常回退(resolved + decode_failed)
        # 构造: 数据记录指向非法偏移内容
        bad = {BEIJING_IP: b"\xff\xff\xff"}
        _install_fixture(bad)
        r = g.lookup_geo(BEIJING_IP)
        record("解码异常回退", r is not None
               and r.get("decode_failed") is True, str(r))

        _clear_fixture()


class TestHaversine:
    def run(self):
        print("[03 haversine]")
        from services.geoip_service import _geo_distance_km
        _install_fixture()
        import services.geoip_service as g
        bj = g.lookup_geo(BEIJING_IP)
        sh = g.lookup_geo(SHANGHAI_IP)
        d = _geo_distance_km(bj, sh)
        record("北京-上海~1068km", d is not None
               and abs(d - 1068) < 60, str(d))
        record("同点0km", _geo_distance_km(bj, bj) == 0.0)
        record("缺经纬度None",
               _geo_distance_km({"latitude": None}, sh) is None)
        _clear_fixture()


class TestVelocitySignal:
    async def run(self):
        print("[04 距离跳变]")
        import services.geoip_service as g
        from repositories.security_repository import (
            Security43Repository,
        )
        repo = Security43Repository()
        _install_fixture()

        # 先北京后上海(2h 窗口内) → 距离 1068km > 500 触发
        r1 = await g.geo_velocity_signal(701, BEIJING_IP)
        record("首请求无信号", r1["hasSignal"] is False, str(r1))
        r2 = await g.geo_velocity_signal(701, SHANGHAI_IP)
        record("跨城触发", r2["hasSignal"] is True
               and r2["score"] == 50.0, str(r2))
        record("城市对留痕", any("北京→上海" in d and "km/2h" in d
                               for d in r2["details"]),
               str(r2["details"]))
        record("距离含留痕", any("1068" in d or "1067" in d
                                 or "1069" in d
                                 for d in r2["details"]),
               str(r2["details"]))

        # 近距(北京→三河 ~55km)不触发
        await repo.start_session_seq(702)
        await g.geo_velocity_signal(702, BEIJING_IP)
        r3 = await g.geo_velocity_signal(702, NEAR_IP)
        record("近距不触发", r3["hasSignal"] is False, str(r3))

        # 阈值环境变量生效(调高到 2000 → 上海也不触发)
        await repo.start_session_seq(703)
        await g.geo_velocity_signal(703, BEIJING_IP)
        os.environ["SECURITY_GEO_VELOCITY_KM"] = "2000"
        r4 = await g.geo_velocity_signal(703, SHANGHAI_IP)
        record("阈值调高不触发", r4["hasSignal"] is False, str(r4))
        os.environ.pop("SECURITY_GEO_VELOCITY_KM", None)

        # 无库零影响(中性)
        _clear_fixture()
        r5 = await g.geo_velocity_signal(704, BEIJING_IP)
        record("无库中性", r5["hasSignal"] is False
               and r5["score"] == 100.0, str(r5))


class TestHttpRoutes:
    async def run(self):
        print("[05 HTTP层]")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes.security_routes import register_security_routes
        from repositories.security_repository import (
            Security43Repository, reputation_status,
        )

        app = FastAPI()
        register_security_routes(app)
        client = TestClient(app)

        # 造一条信誉记录(夹具北京 IP)
        _install_fixture()
        repo = Security43Repository()
        await repo.save_reputation({
            "ip": BEIJING_IP, "score": 80.0,
            "status": reputation_status(80.0),
            "requestCount": 0, "attackCount": 0, "recoverCount": 0,
            "pinned": False, "lastPenaltyAt": None})

        resp = client.get("/api/security/admin/ips",
                          headers={"X-Role": "admin"})
        body = resp.json()
        ips = body.get("ips") or []
        target = [r for r in ips if r.get("ip") == BEIJING_IP]
        record("ips200", resp.status_code == 200, str(resp.status_code))
        record("归属地字段", target and target[0].get("geoCity")
               == "北京", str(target)[:120])

        _clear_fixture()


async def run_all():
    TestRoundTrip().run()
    TestLookupGeo().run()
    TestHaversine().run()
    await TestVelocitySignal().run()
    await TestHttpRoutes().run()


def main():
    reset_store()
    asyncio.run(run_all())
    print()
    print("=" * 62)
    print("\n".join(RESULTS))
    print("=" * 62)
    print(f"总计: {PASS} 通过 / {FAIL} 失败")
    return FAIL


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
