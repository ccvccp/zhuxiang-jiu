/**
 * 智图·AI智能地图大模型 · 前端管理工作台(全域角色时空服务中枢)
 * 四页签: 意图(P0) → 资源(P1) → 调度(P2) → 进化(P3)
 * 地图底座: 百度地图 JS API(AK 经 env 注入; 未配置诚实降级列表视图)
 * 口径: 确定性意图解析 · 建议书模式 · 永不自动执行
 */
import React, { useState, useRef, useEffect } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { ZtAPI, BAIDU_MAP_AK } from '@/api/zt';

type Tab = 'intent' | 'resource' | 'command' | 'evo';

const TABS: { key: Tab; label: string }[] = [
  { key: 'intent', label: '意图' },
  { key: 'resource', label: '资源' },
  { key: 'command', label: '调度' },
  { key: 'evo', label: '进化' },
];

// 春熙路商圈坐标(演示锚点)
const CENTER = { longitude: 104.081, latitude: 30.660 };

// 百度 JS API 仅 H5 可用(依赖 window/document 动态插入 script);
// 小程序端走诚实降级, 不渲染空地图占位
const H5_ENV = process.env.TARO_ENV === 'h5';
const MAP_ON = H5_ENV && !!BAIDU_MAP_AK;

const ZhiTuPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('intent');
  const [mapReady, setMapReady] = useState(false);
  const mapDivRef = useRef<HTMLDivElement>(null);
  const bmapRef = useRef<any>(null);

  // ============ 百度地图底座(AK 降级) ============
  useEffect(() => {
    if (!MAP_ON || mapReady) return;
    // H5 动态加载百度 JS API
    if (typeof window === 'undefined') return;
    const existed = document.getElementById('bmap-sdk');
    const init = () => {
      if (window.BMap && mapDivRef.current) {
        bmapRef.current = new window.BMap.Map(mapDivRef.current);
        const point = new window.BMap.Point(
          CENTER.longitude, CENTER.latitude);
        bmapRef.current.centerAndZoom(point, 13);
        bmapRef.current.addControl(new window.BMap.NavigationControl());
        setMapReady(true);
      }
    };
    if (existed) { init(); return; }
    // 百度 v3.0 loader 默认 document.write 拉主库——页面已加载完毕时
    // 会被浏览器忽略; 须用 callback 参数(JSONP)触发异步加载
    (window as any).__bmapReady = init;
    const script = document.createElement('script');
    script.id = 'bmap-sdk';
    script.src = `https://api.map.baidu.com/api?v=3.0&ak=${BAIDU_MAP_AK}&callback=__bmapReady`;
    script.onerror = () => {
      console.warn('百度地图 JS API 加载失败(检查 AK 白名单)');
    };
    document.head.appendChild(script);
  }, [mapReady]);

  const plotPois = async () => {
    if (!bmapRef.current) {
      Taro.showToast({ title: '地图未就绪(未配 AK 走列表视图)', icon: 'none' });
      return;
    }
    const pois = await ZtAPI.pois().catch(() => []);
    pois.slice(0, 11).forEach((p: any) => {
      const m = new window.BMap.Marker(
        new window.BMap.Point(p.longitude, p.latitude));
      m.setTitle(`${p.name}(${p.poiCode})`);
      bmapRef.current.addOverlay(m);
    });
    Taro.showToast({ title: `已标注 ${Math.min(pois.length, 11)} 个 POI`, icon: 'success' });
  };

  // ============ 意图(P0) ============
  const [query, setQuery] = useState('找附近能吃饭还能买酒的地方');
  const [parsed, setParsed] = useState<any>(null);
  const [searchResult, setSearchResult] = useState<any>(null);
  const [roles, setRoles] = useState<any>(null);

  const runParse = async () => {
    try {
      setParsed(await ZtAPI.parse(query));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };
  const runSearch = async () => {
    try {
      setSearchResult(await ZtAPI.search({
        text: query, longitude: CENTER.longitude,
        latitude: CENTER.latitude, radiusKm: 20,
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };
  const loadRoles = async () => {
    setRoles(await ZtAPI.roles().catch(() => null));
  };

  // ============ 资源(P1) ============
  const [nearby, setNearby] = useState<any>(null);
  const [dockBooking, setDockBooking] = useState<any>(null);
  const [dockStatus, setDockStatus] = useState<any>(null);
  const [warehouse, setWarehouse] = useState<any>(null);
  const [slot, setSlot] = useState('10:00');

  const runNearby = async () => {
    setNearby(await ZtAPI.nearby({
      longitude: CENTER.longitude, latitude: CENTER.latitude,
      radiusKm: 20,
    }).catch(() => null));
  };
  const runDock = async () => {
    try {
      setDockBooking(await ZtAPI.dockBooking({
        supplierName: '蜀粮农业(演示)', slot, goodsType: '高粱',
      }));
      setDockStatus(await ZtAPI.dockStatus().catch(() => null));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };
  const runWarehouse = async () => {
    try {
      setWarehouse(await ZtAPI.warehouseMatch({
        longitude: CENTER.longitude, latitude: CENTER.latitude,
        quantity: 500,
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };

  // ============ 调度(P2) ============
  const [situation, setSituation] = useState<any>(null);
  const [tickets, setTickets] = useState<any[]>([]);
  const [radar, setRadar] = useState<any>(null);

  const runScan = async () => {
    try {
      const r = await ZtAPI.scan();
      setTickets(await ZtAPI.tickets().catch(() => []));
      Taro.showToast({ title: `生成 ${r.generated} 条工单`, icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };
  const runAck = async (tid: number, disposition: string) => {
    try {
      await ZtAPI.ackTicket(tid, disposition);
      setTickets(await ZtAPI.tickets().catch(() => []));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };
  const loadCommand = async () => {
    setSituation(await ZtAPI.situation().catch(() => null));
    setRadar(await ZtAPI.riskRadar().catch(() => null));
  };

  // ============ 进化(P3) ============
  const [sandbox, setSandbox] = useState<any>(null);
  const [performance, setPerformance] = useState<any>(null);
  const [sbName, setSbName] = useState('春熙路旗舰店选址');

  const runSandbox = async () => {
    try {
      setSandbox(await ZtAPI.sandbox({
        name: sbName || '选址方案', longitude: CENTER.longitude,
        latitude: CENTER.latitude, monthlyCost: 50000,
      }));
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 30), icon: 'none' });
    }
  };
  const runPerformance = async () => {
    setPerformance(await ZtAPI.performance().catch(() => null));
  };

  const sevColor = (s: string): string =>
    s === 'high' ? styles.sevHigh : s === 'medium' ? styles.sevMid
      : styles.sevLow;

  return (
    <View className={styles.page}>
      <NavBar title="智图·AI地图" />

      <ScrollView scrollY className={styles.body}>
        <View className={styles.tabBar}>
          {TABS.map(t => (
            <View
              key={t.key}
              className={`${styles.tab} ${tab === t.key ? styles.tabActive : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </View>
          ))}
        </View>

        {/* ============ 地图底座(全域) ============ */}
        <View className={styles.section}>
          <View className={styles.cardTitle}>
            百度地图底座 {MAP_ON ? '(已接入)' : H5_ENV ? '(未配 AK·列表降级)' : '(H5 端能力)'}
          </View>
          {MAP_ON ? (
            <View>
              <View
                ref={mapDivRef as any}
                style={{ width: '100%', height: '300px', borderRadius: '8px' }}
              />
              <View className={styles.runBtn} onClick={plotPois}>
                标注全业务 POI
              </View>
            </View>
          ) : (
            <View className={styles.footNote}>
              {H5_ENV
                ? '百度地图 AK 未配置(TARO_APP_BAIDU_MAP_AK), 诚实降级为 列表+距离视图——不伪造地图渲染; 配置后自动恢复地图底座'
                : '地图底座为 H5 端能力(百度 JS API); 小程序端诚实降级为列表+距离视图'}
            </View>
          )}
        </View>

        {/* ============ 意图 P0 ============ */}
        {tab === 'intent' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>复合意图时空搜索</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={query}
                onInput={e => setQuery(e.detail.value)}
                placeholder="自然语言: 找附近能吃饭还能买酒的地方"
                maxlength={100}
              />
            </View>
            <View className={styles.evoBtns}>
              <View className={styles.evoBtn} onClick={runParse}>解析意图</View>
              <View className={styles.evoBtn} onClick={runSearch}>时空搜索</View>
              <View className={styles.evoBtn} onClick={loadRoles}>六角色</View>
            </View>
            {parsed && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  意图: {parsed.intents.map((i: any) => i.intentName).join(' + ')}
                  {parsed.compound ? '(复合)' : '(单意图)'}
                </View>
                <View className={styles.resItem}>
                  能力向量: {parsed.capabilities.join(' ∧ ')}
                </View>
              </View>
            )}
            {searchResult && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  命中 {searchResult.total} 个 POI ·
                  {searchResult.parsed.compound ? '复合 AND 匹配' : '单能力匹配'}
                </View>
                {(searchResult.results || []).map((r: any) => (
                  <View key={r.poiCode} className={styles.candRow}>
                    <Text>{r.name}</Text>
                    <Text className={styles.candScores}>
                      {r.distance}km · {r.score} 分
                      {r.open ? '' : '(停业)'}
                    </Text>
                  </View>
                ))}
                <View className={styles.resItem}>{searchResult.formula}</View>
              </View>
            )}
            {roles && (
              <View className={styles.resultCard}>
                {roles.roles.map((r: any) => (
                  <View key={r.roleId} className={styles.resItem}>
                    {r.name}: {r.view}
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* ============ 资源 P1 ============ */}
        {tab === 'resource' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>附近 POI(全业务)</View>
            <View className={styles.runBtn} onClick={runNearby}>
              春熙路 20km 圈扫描
            </View>
            {nearby && (nearby.pois || []).map((p: any) => (
              <View key={p.poiCode} className={styles.candRow}>
                <Text>{p.name}</Text>
                <Text className={styles.candScores}>
                  {p.distance}km · {p.poiTypeName}
                  {p.open ? '' : '·停业'}
                </Text>
              </View>
            ))}

            <View className={styles.cardTitle}>月台预约(最短排队)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={slot}
                onInput={e => setSlot(e.detail.value)}
                placeholder="08:00/10:00/14:00/16:00"
                maxlength={5}
              />
              <View className={styles.qaBtn} onClick={runDock}>预约</View>
            </View>
            {dockBooking && (
              <View className={styles.resultCard}>
                <View className={styles.resItem}>
                  分配 {dockBooking.assignedDock.name}
                  (建议; 人工确认后生效)
                </View>
                <View className={styles.resItem}>
                  {dockBooking.navHints[0]} · {dockBooking.settleNote}
                </View>
              </View>
            )}
            {dockStatus && (dockStatus.docks || []).map((d: any) => (
              <View key={d.poiCode} className={styles.candRow}>
                <Text>{d.name}</Text>
                <Text className={styles.candScores}>
                  排队 {d.waiting} · 今日 {d.todayTrucks} 车
                </Text>
              </View>
            ))}

            <View className={styles.cardTitle}>B 端多仓匹配(500 瓶)</View>
            <View className={styles.runBtn} onClick={runWarehouse}>测算</View>
            {warehouse && (warehouse.candidates || []).map((c: any) => (
              <View key={c.poiCode} className={styles.candRow}>
                <Text>{c.name}</Text>
                <Text className={styles.candScores}>
                  {c.distance}km · ¥{c.estFee} · {c.estHours}h · {c.score} 分
                </Text>
              </View>
            ))}
          </View>
        )}

        {/* ============ 调度 P2 ============ */}
        {tab === 'command' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>态势一张图</View>
            <View className={styles.runBtn} onClick={loadCommand}>刷新态势</View>
            {situation && (
              <View className={styles.resultCard}>
                {(situation.poiLayers || []).map((l: any) => (
                  <View key={l.poiType} className={styles.candRow}>
                    <Text>{l.poiTypeName}</Text>
                    <Text className={styles.candScores}>
                      {l.count} 处 · 异常 {l.anomalies}
                    </Text>
                  </View>
                ))}
                <View className={styles.resItem}>
                  物流在途 {situation.logistics.inTransit} 单
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>异常派单(预案工单)</View>
            <View className={styles.runBtn} onClick={runScan}>
              全域扫描(排队/满座/缺货/物流)
            </View>
            {tickets.slice(0, 6).map(t => (
              <View key={t.ticketId} className={styles.alertCard}>
                <View className={styles.resHead}>
                  <View className={`${styles.alertTag} ${sevColor(t.severity)}`}>
                    {t.severityName}·{t.responsible.duty}
                  </View>
                  <View className={styles.resBatch}>{t.status}</View>
                </View>
                <View className={styles.resItem}>{t.detail}</View>
                <View className={styles.resItem}>
                  预案: {t.disposition.action}(人工确认)
                </View>
                {t.status === 'pending' && (
                  <View className={styles.evoBtns}>
                    <View className={styles.evoBtn} onClick={() => runAck(t.ticketId, 'acked')}>确认</View>
                    <View className={styles.evoBtn} onClick={() => runAck(t.ticketId, 'dismissed')}>驳回</View>
                  </View>
                )}
              </View>
            ))}

            {radar && (
              <View className={styles.resultCard}>
                <View className={styles.cardTitle}>风险雷达</View>
                {(radar.items || []).map((i: any, idx: number) => (
                  <View key={idx} className={styles.candRow}>
                    <Text>{i.domain}</Text>
                    <Text className={styles.candScores}>
                      [{i.level}] {i.detail}
                    </Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}

        {/* ============ 进化 P3 ============ */}
        {tab === 'evo' && (
          <View className={styles.section}>
            <View className={styles.cardTitle}>时空战略沙盘(选址)</View>
            <View className={styles.qaInputRow}>
              <Input
                className={styles.qaInput}
                value={sbName}
                onInput={e => setSbName(e.detail.value)}
                placeholder="方案名称"
                maxlength={30}
              />
              <View className={styles.qaBtn} onClick={runSandbox}>推演</View>
            </View>
            {sandbox && (
              <View className={styles.resultCard}>
                <View className={styles.resHead}>
                  <View className={styles.gradeBadge}>
                    {sandbox.prediction.risk} 风险
                  </View>
                  <View className={styles.resScore}>{sandbox.score} 分</View>
                </View>
                <View className={styles.resItem}>
                  人口 {sandbox.factors.populationProxy} · 竞争
                  {sandbox.factors.nearestSameTypeKm ?? '—'}km ·
                  商圈 {sandbox.factors.nearbyPoiCount} POI
                </View>
                <View className={styles.resItem}>
                  月收 ¥{sandbox.prediction.estMonthlyRevenue} -
                  成本 ¥{sandbox.prediction.monthlyCost} =
                  利 ¥{sandbox.prediction.estMonthlyProfit}
                </View>
                <View className={styles.resWarn}>
                  选址为建议书; 决策须管理层人工确认
                </View>
              </View>
            )}

            <View className={styles.cardTitle}>时空绩效画像</View>
            <View className={styles.runBtn} onClick={runPerformance}>效能聚合</View>
            {performance && (performance.byType || []).map((p: any) => (
              <View key={p.poiType} className={styles.candRow}>
                <Text>{p.poiTypeName}</Text>
                <Text className={styles.candScores}>
                  效能 {p.efficiencyProxy} · 开业 {p.openRate * 100}%
                </Text>
              </View>
            ))}

            <View className={styles.cardTitle}>反馈闭环(负样本回流)</View>
            <View className={styles.evoBtns}>
              <View
                className={styles.evoBtn}
                onClick={() => ZtAPI.feedback('sandbox', 'adopted', '沙盘有效')
                  .then(() => Taro.showToast({ title: '已采纳', icon: 'success' }))
                  .catch(() => Taro.showToast({ title: '失败', icon: 'none' }))}
              >👍 沙盘有效</View>
              <View
                className={styles.evoBtn}
                onClick={() => ZtAPI.feedback('sandbox', 'rejected', '口径待校')
                  .then(() => Taro.showToast({ title: '负样本已回流', icon: 'success' }))
                  .catch(() => Taro.showToast({ title: '失败', icon: 'none' }))}
              >👎 拒绝回流</View>
            </View>
            <View className={styles.footNote}>
              智图: 确定性意图解析 · 建议书模式 · 决策权永在人工
            </View>
          </View>
        )}

        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default ZhiTuPage;
