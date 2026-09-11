/**
 * 信值·臻选购物平台(68号) · 前端频道页
 * 五区: 雷达(五维+等级) → 臻选货架(L1) → 导购(SOP) → 邻里臻选(品类聚合)
 *       → 邻里求购(LBS) + 碳档案
 * 宪法口径: 观测面永不关停; 决策面 off 时引导提示(灰度放量 shadow→assist)
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Textarea } from '@tarojs/components';
import Taro, { useDidShow } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import {
  XinzhiAPI, XinzhiRadarVO, PrimeItemVO, PriceDetailVO,
  NeighborShelfVO, GroupbuyVO, CarbonVO, ModeVO, GuideReplyVO,
  xinzhiGradeName, xinzhiDimName,
} from '@/api/xinzhi';
import { getMemberId } from '@/services/auth-service';

type Tab = 'shelf' | 'neighbor';

const TABS: { key: Tab; label: string }[] = [
  { key: 'shelf', label: '臻选货架' },
  { key: 'neighbor', label: '邻里社区' },
];

// 预设地点(与叫帮/代驾页一致)
const PRESETS = [
  { name: '泉城广场(市中心)', lat: 36.6634, lng: 117.0268 },
  { name: '竹韵大酒店(历下区)', lat: 36.6612, lng: 117.1201 },
];
const LOC_IDX = 1;

// 反馈场景标签(四步闭环)
const FB_SCENES = [
  { key: 'product', label: '商品问题', tags: ['价格不合理', '描述不符'] },
  { key: 'radar', label: '信值疑问', tags: ['分数不合理', '维度不理解'] },
  { key: 'guide', label: '导购反馈', tags: ['推荐不合适', '解释不清楚'] },
];

const formatMoney = (n: number): string => `¥${Number(n || 0).toFixed(2)}`;

const XinzhiPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('shelf');
  const [loading, setLoading] = useState(true);
  const myId = Number(getMemberId() || 0);

  // 雷达
  const [radar, setRadar] = useState<XinzhiRadarVO | null>(null);
  // 货架
  const [prime, setPrime] = useState<PrimeItemVO[]>([]);
  const [priceDetail, setPriceDetail] = useState<PriceDetailVO | null>(null);
  // 导购
  const [guideQuery, setGuideQuery] = useState('');
  const [guideReply, setGuideReply] = useState<GuideReplyVO | null>(null);
  // 邻里
  const [neighbor, setNeighbor] = useState<NeighborShelfVO | null>(null);
  const [groupbuys, setGroupbuys] = useState<GroupbuyVO[]>([]);
  const [gbTitle, setGbTitle] = useState('');
  const [gbUrgent, setGbUrgent] = useState(false);
  const [gbSubmitting, setGbSubmitting] = useState(false);
  // 碳档案
  const [carbon, setCarbon] = useState<CarbonVO | null>(null);
  // 灰度
  const [mode, setMode] = useState<ModeVO | null>(null);
  // 反馈弹层
  const [fbOpen, setFbOpen] = useState(false);
  const [fbScene, setFbScene] = useState('product');
  const [fbContent, setFbContent] = useState('');
  const [fbResult, setFbResult] = useState<string>('');

  const loadAll = useCallback(async () => {
    try {
      const [m, r] = await Promise.all([
        XinzhiAPI.mode().catch(() => null),
        myId ? XinzhiAPI.radar().catch(() => null) : Promise.resolve(null),
      ]);
      setMode(m);
      setRadar(r);
      const [p, nb, gb, cb] = await Promise.all([
        XinzhiAPI.prime(10).catch(() => [] as PrimeItemVO[]),
        XinzhiAPI.neighbor().catch(() => null),
        XinzhiAPI.groupbuyHall(PRESETS[LOC_IDX].lng, PRESETS[LOC_IDX].lat, 10)
          .catch(() => [] as GroupbuyVO[]),
        myId ? XinzhiAPI.carbon().catch(() => null) : Promise.resolve(null),
      ]);
      setPrime(p);
      setNeighbor(nb);
      setGroupbuys(gb);
      setCarbon(cb);
    } finally {
      setLoading(false);
    }
  }, [myId]);

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useDidShow(() => {
    loadAll();
  });

  /** 查看价格构成(透明定价) */
  const showPrice = async (productId: string) => {
    try {
      const d = await XinzhiAPI.price(productId);
      setPriceDetail(d);
    } catch (e: any) {
      Taro.showToast({ title: e?.message || '查询失败', icon: 'none' });
    }
  };

  /** 导购问答(决策面——off 时提示灰度) */
  const askGuide = async () => {
    if (!prime.length) {
      Taro.showToast({ title: '货架暂无商品', icon: 'none' });
      return;
    }
    try {
      const reply = await XinzhiAPI.guide(prime[0].productId, guideQuery || '推荐');
      setGuideReply(reply);
    } catch (e: any) {
      const msg = String(e?.message || e || '');
      if (msg.includes('决策面') || msg.includes('409')) {
        Taro.showModal({
          title: '灰度开放中',
          content: '导购问答处于灰度观察期(XINZHI_MODE=off), 数据积累达标后开放。您的雷达/价格/反馈等观测功能不受影响。',
          showCancel: false,
        });
      } else {
        Taro.showToast({ title: msg.slice(0, 30) || '查询失败', icon: 'none' });
      }
    }
  };

  /** 发布求购(决策面) */
  const submitGroupbuy = async () => {
    if (!gbTitle.trim()) {
      Taro.showToast({ title: '请填写求购内容', icon: 'none' });
      return;
    }
    setGbSubmitting(true);
    try {
      const loc = PRESETS[LOC_IDX];
      await XinzhiAPI.publishGroupbuy({
        title: gbTitle.trim(),
        quantity: 1,
        urgency: gbUrgent ? 'urgent' : 'normal',
        longitude: loc.lng,
        latitude: loc.lat,
        address: loc.name,
      });
      Taro.showToast({ title: '已发布, 等待邻里响应', icon: 'success' });
      setGbTitle('');
      setGroupbuys(await XinzhiAPI.groupbuyHall(loc.lng, loc.lat, 10));
    } catch (e: any) {
      const msg = String(e?.message || e || '');
      if (msg.includes('决策面') || msg.includes('409')) {
        Taro.showModal({
          title: '灰度开放中',
          content: '邻里求购处于灰度观察期, 达标后开放。您可浏览大厅与邻里频道(观测面)。',
          showCancel: false,
        });
      } else {
        Taro.showToast({ title: msg.slice(0, 30), icon: 'none' });
      }
    } finally {
      setGbSubmitting(false);
    }
  };

  /** 响应求购 */
  const respondGb = async (gb: GroupbuyVO) => {
    try {
      await XinzhiAPI.respondGroupbuy(gb.groupbuyId);
      Taro.showToast({ title: '已响应', icon: 'success' });
      const loc = PRESETS[LOC_IDX];
      setGroupbuys(await XinzhiAPI.groupbuyHall(loc.lng, loc.lat, 10));
    } catch (e: any) {
      const msg = String(e?.message || e || '');
      Taro.showToast({
        title: msg.includes('决策面') ? '灰度观察期' : (msg.slice(0, 30) || '失败'),
        icon: 'none',
      });
    }
  };

  /** 提交反馈(永不关停) */
  const submitFeedback = async () => {
    const scene = FB_SCENES.find(s => s.key === fbScene)!;
    try {
      const fb = await XinzhiAPI.submitFeedback(fbScene, scene.tags, fbContent);
      setFbResult(fb.level === 'L1'
        ? `已记录并自动回复: ${fb.autoReply}`
        : `已受理(${fb.sla}), 路由至${fb.routedTo}`);
      setFbContent('');
    } catch (e: any) {
      Taro.showToast({ title: '提交失败', icon: 'none' });
    }
  };

  const gradeColor = (g: string): string =>
    g === 'S' ? styles.gradeS : g === 'A' ? styles.gradeA
      : g === 'B' ? styles.gradeB : styles.gradeD;

  return (
    <View className={styles.page}>
      <NavBar title="信值·臻选" />

      <ScrollView scrollY className={styles.body}>
        {/* ============ 一、信值雷达卡 ============ */}
        <View className={styles.radarCard}>
          <View className={styles.radarHeader}>
            <View>
              <View className={styles.radarTitle}>我的信值雷达</View>
              <View className={styles.radarSub}>
                {radar?.coldStart ? '新用户保护期 · 权重已切换' : '五维信用画像 · 全链可解释'}
              </View>
            </View>
            {radar && (
              <View className={`${styles.gradeBadge} ${gradeColor(radar.grade)}`}>
                {xinzhiGradeName(radar.grade)}
              </View>
            )}
          </View>
          {radar ? (
            <>
              <View className={styles.radarTotal}>
                <Text className={styles.radarTotalNum}>{radar.totalScore}</Text>
                <Text className={styles.radarTotalUnit}>分</Text>
              </View>
              {radar.circuitBroken && (
                <View className={styles.circuitTip}>
                  熔断中: 存在维度低于 40, 总分暂封顶 59
                </View>
              )}
              <View className={styles.dimGrid}>
                {(radar.dimensions || []).map(d => (
                  <View key={d.key} className={styles.dimItem}>
                    <View className={styles.dimScore}>{d.score}</View>
                    <View className={styles.dimLabel}>{xinzhiDimName(d.key)}</View>
                    <View className={styles.dimBar}>
                      <View className={styles.dimBarFill} style={{ width: `${Math.min(100, d.score)}%` }} />
                    </View>
                    {d.factors?.length > 0 && (
                      <View className={styles.dimFactor}>{d.factors[0]}</View>
                    )}
                  </View>
                ))}
              </View>
            </>
          ) : (
            <View className={styles.loginTip}>
              {myId ? '雷达计算中…' : '登录后查看您的五维信值画像'}
            </View>
          )}
          {carbon && (
            <View className={styles.carbonRow}>
              🌱 碳积分 {carbon.carbonGrams >= 1000
                ? `${(carbon.carbonGrams / 1000).toFixed(1)}kg` : `${carbon.carbonGrams.toFixed(0)}g`}
              (互助 {carbon.helpOrders} 单 · 拼单 {carbon.groupbuys} 次 · 只读不可交易)
            </View>
          )}
        </View>

        {/* 标签页 */}
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
          <View className={styles.fbBtn} onClick={() => { setFbOpen(!fbOpen); setFbResult(''); }}>
            反馈
          </View>
        </View>

        {/* 反馈弹层(永不关停) */}
        {fbOpen && (
          <View className={styles.fbPanel}>
            <View className={styles.fbScenes}>
              {FB_SCENES.map(s => (
                <View
                  key={s.key}
                  className={`${styles.fbScene} ${fbScene === s.key ? styles.fbSceneActive : ''}`}
                  onClick={() => setFbScene(s.key)}
                >
                  {s.label}
                </View>
              ))}
            </View>
            <Textarea
              className={styles.fbInput}
              value={fbContent}
              onInput={e => setFbContent(e.detail.value)}
              placeholder="说说您遇到的問題(15 分钟紧急通道自动分流)…"
              maxlength={500}
            />
            <View className={styles.fbSubmit} onClick={submitFeedback}>提交反馈</View>
            {fbResult && <View className={styles.fbResult}>{fbResult}</View>}
          </View>
        )}

        {/* ============ 二、臻选货架 ============ */}
        {tab === 'shelf' && (
          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              臻选货架
              <Text className={styles.sectionNote}>三维评分 · 信值加权排序 · 价格全透明</Text>
            </View>
            {prime.length === 0 && !loading && (
              <View className={styles.empty}>货架整理中…</View>
            )}
            {prime.map(item => (
              <View key={item.scoreSeq} className={styles.productCard}>
                <View className={styles.productHead}>
                  <View className={styles.productName}>{item.productName}</View>
                  <View className={`${styles.productGrade} ${item.grade === 'L1' ? styles.gradeS : styles.gradeB}`}>
                    {item.grade}
                  </View>
                </View>
                <View className={styles.scoreRow}>
                  <View className={styles.scoreItem}>
                    <View className={styles.scoreNum}>{item.fit}</View>
                    <View className={styles.scoreLbl}>契合</View>
                  </View>
                  <View className={styles.scoreItem}>
                    <View className={styles.scoreNum}>{item.safety}</View>
                    <View className={styles.scoreLbl}>安全</View>
                  </View>
                  <View className={styles.scoreItem}>
                    <View className={styles.scoreNum}>{item.conversion}</View>
                    <View className={styles.scoreLbl}>转化</View>
                  </View>
                  <View className={styles.scoreItem}>
                    <View className={styles.scoreNum}>{item.valueScore}</View>
                    <View className={styles.scoreLbl}>价值分</View>
                  </View>
                </View>
                <View className={styles.priceBtn} onClick={() => showPrice(item.productId)}>
                  查看价格构成
                </View>
              </View>
            ))}

            {/* 价格构成弹层(透明定价) */}
            {priceDetail && (
              <View className={styles.pricePanel}>
                <View className={styles.pricePanelTitle}>
                  价格构成 · {priceDetail.productName}
                </View>
                <View className={styles.priceRows}>
                  <View className={styles.priceRow}>
                    <Text>原价</Text>
                    <Text>{formatMoney(priceDetail.basePrice)}</Text>
                  </View>
                  <View className={styles.priceRow}>
                    <Text>信值抵扣(等级{priceDetail.grade} α={priceDetail.xinzhiAlpha})</Text>
                    <Text className={styles.priceDeduct}>-{formatMoney(priceDetail.xinzhiCredit)}</Text>
                  </View>
                  <View className={styles.priceRow}>
                    <Text>三因子折扣</Text>
                    <Text className={styles.priceDeduct}>
                      -{formatMoney(priceDetail.basePrice - priceDetail.afterThreeFactor)}
                    </Text>
                  </View>
                  <View className={`${styles.priceRow} ${styles.priceFinal}`}>
                    <Text>实付</Text>
                    <Text>{formatMoney(priceDetail.finalPrice)}</Text>
                  </View>
                </View>
                {priceDetail.floored && (
                  <View className={styles.priceNote}>已触达平台地板保护(基准价 7 折)</View>
                )}
                {priceDetail.auditFlag && (
                  <View className={styles.priceNote}>
                    ⚠ 存在跨会员价差记录, 已进入审计通道(杀熟零容忍)
                  </View>
                )}
                <View className={styles.priceClose} onClick={() => setPriceDetail(null)}>收起</View>
              </View>
            )}

            {/* 导购问答 */}
            <View className={styles.guideSection}>
              <View className={styles.guideTitle}>🧑‍🌾 小竹臻选导购</View>
              <View className={styles.guideSub}>
                数字全部来自实时查询 · 不承诺降价 · 风险如实告知
              </View>
              <View className={styles.guideInputRow}>
                <Input
                  className={styles.guideInput}
                  value={guideQuery}
                  onInput={e => setGuideQuery(e.detail.value)}
                  placeholder={`问问${prime[0]?.productName?.slice(0, 6) || '这款酒'}: 多少钱? 适合我吗?`}
                />
                <View className={styles.guideBtn} onClick={askGuide}>问小竹</View>
              </View>
              {guideReply && (
                <View className={styles.guideReply}>
                  {guideReply.reply}
                  {guideReply.xinzhiMode && (
                    <View className={styles.guideMode}>灰度态: {guideReply.xinzhiMode}</View>
                  )}
                </View>
              )}
            </View>
          </View>
        )}

        {/* ============ 三、邻里社区 ============ */}
        {tab === 'neighbor' && (
          <View className={styles.section}>
            <View className={styles.sectionTitle}>
              邻里臻选
              <Text className={styles.sectionNote}>
                {neighbor ? `品类聚合 · 匿名门槛 K=${neighbor.anonymityK}(零个体数据)` : ''}
              </Text>
            </View>
            {neighbor && neighbor.categories.length > 0 && (
              <View className={styles.nbGrid}>
                {neighbor.categories.map((c, i) => (
                  <View key={`${c.series}-${i}`} className={styles.nbItem}>
                    <View className={styles.nbSeries}>{c.series}</View>
                    <View className={styles.nbCount}>{c.buyerCount} 人在买</View>
                    <View className={styles.nbOrders}>{c.orderCount} 单</View>
                  </View>
                ))}
              </View>
            )}
            {neighbor && neighbor.categories.length === 0 && (
              <View className={styles.empty}>
                同城聚合数据积累中(不足 {neighbor.anonymityK} 人的品类不展示——保护隐私)
              </View>
            )}

            <View className={styles.sectionTitle} style={{ marginTop: '24rpx' }}>
              邻里求购大厅
              <Text className={styles.sectionNote}>紧急优先 · 距离优先 · 响应仅脱敏昵称</Text>
            </View>

            {/* 发布求购 */}
            <View className={styles.gbPublish}>
              <Input
                className={styles.gbInput}
                value={gbTitle}
                onInput={e => setGbTitle(e.detail.value)}
                placeholder="想买什么? 发个求购让邻里搭把手…"
                maxlength={60}
              />
              <View className={styles.gbPublishRow}>
                <View
                  className={`${styles.urgentTag} ${gbUrgent ? styles.urgentTagOn : ''}`}
                  onClick={() => setGbUrgent(!gbUrgent)}
                >
                  {gbUrgent ? '🔴 紧急' : '⚪ 普通'}
                </View>
                <View
                  className={styles.gbBtn}
                  onClick={submitGroupbuy}
                >
                  {gbSubmitting ? '发布中…' : '发布求购'}
                </View>
              </View>
            </View>

            {/* 求购列表 */}
            {groupbuys.length === 0 && !loading && (
              <View className={styles.empty}>附近暂无求购——发第一个, 让邻里看到您</View>
            )}
            {groupbuys.map(gb => (
              <View key={gb.groupbuyId} className={styles.gbCard}>
                <View className={styles.gbHead}>
                  {gb.urgency === 'urgent' && <View className={styles.gbUrgent}>紧急</View>}
                  <View className={styles.gbTitle}>{gb.title}</View>
                  {gb.distanceKm != null && (
                    <View className={styles.gbDist}>{gb.distanceKm}km</View>
                  )}
                </View>
                <View className={styles.gbMeta}>
                  {gb.publisherMasked} · {gb.responderCount} 人响应
                  {gb.series && ` · ${gb.series}`}
                  {gb.closed && ' · 已解决'}
                </View>
                {!gb.closed && (
                  <View className={styles.gbRespondBtn} onClick={() => respondGb(gb)}>
                    我能帮忙
                  </View>
                )}
                {gb.closed && gb.carbonGrams != null && (
                  <View className={styles.gbCarbon}>🌱 本单碳减排 {gb.carbonGrams}g</View>
                )}
              </View>
            ))}
          </View>
        )}

        {/* 灰度说明脚注 */}
        {mode && (
          <View className={styles.modeFooter}>
            {mode.mode === 'off'
              ? '部分功能灰度开放中(导购问答/求购发布), 观测功能全量可用'
              : `当前灰度态: ${mode.mode}${mode.paused ? '(护栏保护暂停)' : ''}`}
            {' · '}信值透明是宪法, 不是功能
          </View>
        )}

        <View className={styles.bottomSpace} />
      </ScrollView>
    </View>
  );
};

export default XinzhiPage;
