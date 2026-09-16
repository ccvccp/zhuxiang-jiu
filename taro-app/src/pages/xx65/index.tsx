/**
 * 65号·智能开店工作台 · 四页签: 开店 / 内容工坊 / 营销 / 治理
 * 数据来源: 后端 /api/xx65/*(双角色 member+admin)
 * 口径: 观测面常开; 决策面(意图/开店/认领/激活/草稿/发布/活动)
 *       受 XX65_MODE 门控(off 409 友好降级);
 *       关店/人工兜底(S6)/巡检不受开关影响(宪法豁免面);
 *       S1-S8 刚性规则(LLM 禁入判定链, 全确定性)。
 */
import React, { useState, useEffect, useCallback } from 'react';
import { View, Text, ScrollView, Input, Textarea } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { getSession } from '@/services/auth-service';
import {
  Xx65API, IntentVO, ShopVO, DraftVO, ProductVO,
  RecommendationVO, CampaignVO, CoachTipVO,
  SHOP_STATES, DRAFT_STATES,
} from '@/api/xx65';

type Tab = 'shop' | 'content' | 'campaign' | 'govern';

const TABS: { key: Tab; label: string }[] = [
  { key: 'shop', label: '开店' },
  { key: 'content', label: '内容工坊' },
  { key: 'campaign', label: '营销' },
  { key: 'govern', label: '治理' },
];

/** 决策面 409 友好降级(对齐 trust/zw 范式) */
function decisionErr(e: any): string {
  const msg = String(e?.message || e);
  if (msg.includes('409') || msg.includes('决策')
    || msg.includes('MODE') || msg.includes('开关')
    || msg.includes('暂停')) {
    return '智能开店决策功能暂未开放(决策面关闭)，敬请期待';
  }
  return msg.slice(0, 40);
}

const Xx65WorkbenchPage: React.FC = () => {
  const [tab, setTab] = useState<Tab>('shop');
  // 总览
  const [mode, setMode] = useState<string>('');
  const [myShop, setMyShop] = useState<ShopVO | null>(null);
  // 开店流程
  const [intentText, setIntentText] = useState('');
  const [intent, setIntent] = useState<IntentVO | null>(null);
  const [shopDraft, setShopDraft] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  // 内容
  const [draftForm, setDraftForm] = useState(
    { productName: '', description: '', price: '' });
  const [draft, setDraft] = useState<DraftVO | null>(null);
  const [products, setProducts] = useState<ProductVO[]>([]);
  // 营销
  const [recommendations, setRecommendations] = useState<RecommendationVO[]>([]);
  const [selStrategy, setSelStrategy] = useState('');
  const [campaigns, setCampaigns] = useState<CampaignVO[]>([]);
  // 治理
  const [health, setHealth] = useState<any>(null);
  const [tips, setTips] = useState<CoachTipVO[]>([]);
  // 下单窗口(S4 双轨展示) / 治理 admin 区
  const [orderWin, setOrderWin] = useState<any>(null);
  const [orderWinPid, setOrderWinPid] = useState(0);
  const [rtResult, setRtResult] = useState<any>(null);
  const isAdmin = getSession()?.role === 'admin';

  const shopId = myShop?.shopId || 0;

  const loadAll = useCallback(async () => {
    try {
      const [ms, shops] = await Promise.all([
        Xx65API.modelStatus().catch(() => null),
        Xx65API.myShops().catch(() => [] as ShopVO[]),
      ]);
      setMode(String(ms?.mode || ''));
      const mine = (shops || []).find(
        (s) => s.status !== 'closed') || null;
      setMyShop(mine);
      if (mine?.shopId) {
        const [ps, cs, h, t] = await Promise.all([
          Xx65API.products(mine.shopId).catch(() => [] as ProductVO[]),
          Xx65API.campaigns(mine.shopId).catch(() => [] as CampaignVO[]),
          Xx65API.shopHealth(mine.shopId).catch(() => null),
          Xx65API.coachTips(mine.shopId).catch(() => [] as CoachTipVO[]),
        ]);
        setProducts(ps);
        setCampaigns(cs);
        setHealth(h);
        setTips(t);
      }
    } catch (_) { /* 观测面 best-effort */ }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  // ---------- 开店流程 ----------
  const handleParse = async () => {
    if (busy || !intentText.trim()) return;
    setBusy(true);
    try {
      setIntent(await Xx65API.parseIntent(intentText.trim()));
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handleApply = async () => {
    if (busy || !intent?.intentId) return;
    setBusy(true);
    try {
      const res = await Xx65API.applyShop(intent.intentId);
      setShopDraft(res);
      Taro.showToast({ title: '预检通过，请完成合规问卷', icon: 'none' });
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handleClaim = async () => {
    if (busy || !shopDraft?.shopId) return;
    setBusy(true);
    try {
      const answers: Record<string, string> = {};
      (shopDraft.complianceQuestions || []).forEach(
        (q: string) => { answers[q] = '否'; });
      const res = await Xx65API.claimShop(shopDraft.shopId, answers);
      setShopDraft({ ...shopDraft, status: res?.status || 'claimed' });
      Taro.showToast({ title: '认领成功', icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handleActivate = async () => {
    if (busy || !shopDraft?.shopId) return;
    setBusy(true);
    try {
      const res = await Xx65API.activateShop(shopDraft.shopId);
      setMyShop({
        shopId: shopDraft.shopId, ownerId: 0,
        status: res?.status || 'active',
        category: intent?.category || '',
        categoryLabel: intent?.categoryLabel || '',
      });
      setShopDraft(null);
      Taro.showToast({ title: '店铺已激活，开始经营', icon: 'success' });
      loadAll();
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handleClose = async () => {
    if (busy || !shopId) return;
    setBusy(true);
    try {
      await Xx65API.closeShop(shopId);
      setMyShop(null);
      Taro.showToast({ title: '店铺已关闭(经营者权利，不受开关影响)', icon: 'none' });
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 40), icon: 'none' });
    } finally { setBusy(false); }
  };

  // ---------- 内容工坊 ----------
  const handleDraft = async () => {
    if (busy || !shopId || !draftForm.productName.trim()) return;
    setBusy(true);
    try {
      setDraft(await Xx65API.createDraft({
        shopId, productName: draftForm.productName.trim(),
        description: draftForm.description.trim(),
        price: Number(draftForm.price || 0),
      }));
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handlePublish = async () => {
    if (busy || !draft?.draftId) return;
    setBusy(true);
    try {
      await Xx65API.publishDraft(draft.draftId);
      Taro.showToast({ title: '已发布(S1 终审通过)', icon: 'success' });
      setDraft(null);
      setDraftForm({ productName: '', description: '', price: '' });
      setProducts(await Xx65API.products(shopId).catch(() => []));
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handleHumanReview = async () => {
    if (busy || !draft?.draftId) return;
    setBusy(true);
    try {
      await Xx65API.humanReview(draft.draftId, '店主申诉转人工(S6)');
      Taro.showToast({ title: '已转人工审核(不受开关影响)', icon: 'none' });
      setDraft(null);
    } catch (e: any) {
      Taro.showToast({ title: String(e?.message || e).slice(0, 40), icon: 'none' });
    } finally { setBusy(false); }
  };

  // ---------- 营销 ----------
  const handleRecommend = async () => {
    if (busy || !shopId || !products.length) return;
    setBusy(true);
    try {
      setRecommendations(await Xx65API.recommendCampaigns(
        shopId, products[0].productId));
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handleCreateCampaign = async () => {
    if (busy || !shopId || !selStrategy || !products.length) return;
    setBusy(true);
    try {
      await Xx65API.createCampaign({
        shopId, productId: products[0].productId, strategy: selStrategy });
      Taro.showToast({ title: '活动已创建(5 分钟内可撤销)', icon: 'success' });
      setSelStrategy('');
      setCampaigns(await Xx65API.campaigns(shopId).catch(() => []));
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handleRevoke = async (cid: number) => {
    if (busy) return;
    setBusy(true);
    try {
      await Xx65API.revokeCampaign(cid);
      Taro.showToast({ title: '已撤销(S5 窗口)', icon: 'success' });
      setCampaigns(await Xx65API.campaigns(shopId).catch(() => []));
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  // ---------- 下单窗口(S4 双轨展示) ----------
  const handleOrderWindow = async (pid: number) => {
    if (busy) return;
    if (orderWinPid === pid) { setOrderWinPid(0); setOrderWin(null); return; }
    setBusy(true);
    try {
      setOrderWin(await Xx65API.orderWindow(pid));
      setOrderWinPid(pid);
    } catch (e: any) {
      Taro.showToast({
        title: String(e?.message || e).slice(0, 40), icon: 'none' });
    } finally { setBusy(false); }
  };

  // ---------- 治理 admin 区 ----------
  const handleQuotaAdjust = async (
    direction: 'uplift' | 'downgrade') => {
    if (busy || !shopId) return;
    setBusy(true);
    try {
      await Xx65API.quotaAdjust(shopId, direction);
      Taro.showToast({
        title: `已提交${direction === 'uplift' ? '升档' : '降档'}建议(经 46号审批轨)`,
        icon: 'none' });
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const handleRedteam = async () => {
    if (busy) return;
    setBusy(true);
    try {
      setRtResult(await Xx65API.redteam());
      Taro.showToast({ title: '红队七向量仿真完成', icon: 'success' });
    } catch (e: any) {
      Taro.showToast({ title: decisionErr(e), icon: 'none' });
    } finally { setBusy(false); }
  };

  const activeShop = myShop?.status === 'active';

  return (
    <View className={styles.page}>
      <NavBar title="智能开店工作台" back />
      <View className={styles.tabs}>
        {TABS.map((t) => (
          <View
            key={t.key}
            className={tab === t.key ? styles.tabActive : styles.tab}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </View>
        ))}
      </View>

      <ScrollView scrollY className={styles.scrollView}>
        {/* ============ 开店 ============ */}
        {tab === 'shop' && (
          <View>
            <View className={styles.modeCard}>
              <View className={styles.modeLabel}>模型运行模式</View>
              <View className={styles.modeValue}>{mode || '--'}</View>
              <View className={styles.modeDesc}>
                S1-S8 刚性规则宪法 · 六态店铺状态机 · 判定链 LLM 禁入(全确定性)
              </View>
            </View>

            {/* 我的店铺 */}
            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>我的店铺</View>
                {myShop && (
                  <Text className={styles.sectionCount}>
                    {SHOP_STATES[myShop.status] || myShop.status}
                  </Text>
                )}
              </View>
              {myShop ? (
                <View>
                  <View className={styles.rowBetween}>
                    <Text>#{myShop.shopId} {myShop.categoryLabel || myShop.category}</Text>
                    <Text className={styles.tag}>{SHOP_STATES[myShop.status] || myShop.status}</Text>
                  </View>
                  {activeShop && (
                    <View className={styles.btnGhost} onClick={handleClose}>
                      自主关店(经营者权利 · 不受开关影响)
                    </View>
                  )}
                </View>
              ) : (
                <View className={styles.empty}>尚未开店——填写经营意图开始</View>
              )}
            </View>

            {/* 意图解析 */}
            <View className={styles.card}>
              <View className={styles.cardTitle}>开店意图解析(确定性路由)</View>
              <Textarea
                className={styles.textarea}
                value={intentText}
                onInput={(e) => setIntentText(e.detail.value)}
                placeholder="描述你想经营的内容，如：手工木雕定制"
                maxlength={200}
              />
              <View className={styles.btnPrimary} onClick={handleParse}>
                {busy ? '解析中…' : '解析意图'}
              </View>

              {intent && (
                <View className={styles.resultCard}>
                  <View className={styles.rowBetween}>
                    <Text className={styles.metricValue}>{intent.categoryLabel || intent.category}</Text>
                    <Text className={styles.tag}>{intent.fallback ? '综合(回退)' : '命中'}</Text>
                  </View>
                  <View className={styles.note}>开店门槛: 信用等级 ≥ {intent.minLevel}(tier 加严映射)</View>
                  {intent.complianceQuestions.length > 0 && (
                    <View className={styles.note}>
                      合规问卷: {intent.complianceQuestions.join(' / ')}
                    </View>
                  )}
                  {!myShop && !shopDraft && (
                    <View className={styles.btnPrimary} onClick={handleApply}>
                      申请开店(S2 信值准入预检)
                    </View>
                  )}
                </View>
              )}

              {/* 认领/激活流程 */}
              {shopDraft && (
                <View className={styles.resultCard}>
                  <View className={styles.note}>
                    店铺 #{shopDraft.shopId} · 状态: 预检通过——完成合规问卷后认领
                  </View>
                  {shopDraft.status !== 'claimed' ? (
                    <View className={styles.btnPrimary} onClick={handleClaim}>
                      一键认领(合规问卷全部如实作答)
                    </View>
                  ) : (
                    <View className={styles.btnPrimary} onClick={handleActivate}>
                      激活店铺(开始经营)
                    </View>
                  )}
                </View>
              )}
            </View>
          </View>
        )}

        {/* ============ 内容工坊 ============ */}
        {tab === 'content' && (
          <View>
            <View className={styles.card}>
              <View className={styles.cardTitle}>内容草稿生成</View>
              {!activeShop && (
                <View className={styles.empty}>须先激活店铺(开店页签完成流程)</View>
              )}
              {activeShop && (
                <View>
                  <View className={styles.fieldLabel}>商品名称</View>
                  <Input
                    className={styles.input}
                    value={draftForm.productName}
                    onInput={(e) => setDraftForm(
                      { ...draftForm, productName: e.detail.value })}
                    placeholder="如: 定制木雕摆件"
                  />
                  <View className={styles.fieldLabel}>商品描述</View>
                  <Textarea
                    className={styles.textarea}
                    value={draftForm.description}
                    onInput={(e) => setDraftForm(
                      { ...draftForm, description: e.detail.value })}
                    placeholder="广告法极限词将自动替换并留痕"
                    maxlength={300}
                  />
                  <View className={styles.fieldLabel}>价格(元)</View>
                  <Input
                    className={styles.input}
                    type="digit"
                    value={draftForm.price}
                    onInput={(e) => setDraftForm(
                      { ...draftForm, price: e.detail.value })}
                    placeholder="100"
                  />
                  <View className={styles.btnPrimary} onClick={handleDraft}>
                    {busy ? '生成中…' : '生成草稿(确定性模板 · LLM 禁入)'}
                  </View>
                </View>
              )}

              {draft && (
                <View className={styles.resultCard}>
                  <View className={styles.rowBetween}>
                    <Text className={styles.metricValue}>{draft.productName}</Text>
                    <Text className={styles.tag}>{DRAFT_STATES[draft.status] || draft.status}</Text>
                  </View>
                  <View className={styles.note}>价格: ¥{draft.price} · 轨道: {draft.llmTrack}(rule 确定性)</View>
                  {draft.replacements.length > 0 && (
                    <View className={styles.note}>
                      禁词替换: {draft.replacements
                        .map((r) => `${r.from}→${r.to}`).join('，')}
                    </View>
                  )}
                  {draft.watermark && (
                    <View className={styles.note}>溯源水印: {draft.watermark}</View>
                  )}
                  <View className={styles.btnPrimary} onClick={handlePublish}>
                    确认发布(S1 终审 · 整单互斥 R2)
                  </View>
                  <View className={styles.btnGhost} onClick={handleHumanReview}>
                    转人工审核(S6 · 不受开关影响)
                  </View>
                </View>
              )}
            </View>

            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>商品列表</View>
                <Text className={styles.sectionCount}>{products.length} 件</Text>
              </View>
              {products.length === 0 && (
                <View className={styles.empty}>暂无商品——生成草稿并发布</View>
              )}
              {products.map((p) => (
                <View key={p.productId}>
                  <View
                    className={styles.listItem}
                    onClick={() => p.status === 'published'
                      && handleOrderWindow(p.productId)}
                  >
                    <View className={styles.listMain}>
                      <View className={styles.listTitle}>
                        {p.productName}
                        {p.status === 'published' && (
                          <Text className={styles.tag}>下单窗口</Text>
                        )}
                      </View>
                      <View className={styles.listDesc}>
                        ¥{p.price} · {p.status === 'published' ? '已发布' : p.status}
                      </View>
                    </View>
                  </View>
                  {orderWinPid === p.productId && orderWin && (
                    <View className={styles.resultCard}>
                      <View className={styles.rowBetween}>
                        <Text className={styles.metricValue}>双轨定价(S4)</Text>
                        <Text className={styles.note}>扣减以 64号为准(S3)</Text>
                      </View>
                      <View className={styles.note}>
                        现金 ¥{orderWin.dualTrack?.cashValue ?? '--'} +
                        信值 {orderWin.dualTrack?.trustValue ?? '--'}
                        (30% 对齐 64号 R1)
                      </View>
                      <View className={styles.note}>
                        单次占比 {orderWin.quotaProgress?.singleRatio != null
                          ? `${Math.round(orderWin.quotaProgress.singleRatio * 100)}%` : '--'}
                        · 累计占比 {orderWin.quotaProgress?.cumulativeRatio != null
                          ? `${Math.round(orderWin.quotaProgress.cumulativeRatio * 100)}%` : '--'}
                      </View>
                      {(orderWin.warnings || []).length > 0 && (
                        <View className={styles.note}>
                          ⚠️ {(orderWin.warnings || []).join('；')}
                        </View>
                      )}
                    </View>
                  )}
                </View>
              ))}
            </View>
          </View>
        )}

        {/* ============ 营销 ============ */}
        {tab === 'campaign' && (
          <View>
            <View className={styles.card}>
              <View className={styles.cardTitle}>活动策略推荐(三因子+ROI 双算)</View>
              {activeShop ? (
                <View className={styles.btnPrimary} onClick={handleRecommend}>
                  {busy ? '计算中…' : products.length
                    ? `获取推荐(基于「${products[0].productName}」)` : '请先发布商品'}
                </View>
              ) : (
                <View className={styles.empty}>须先激活店铺</View>
              )}

              {recommendations.map((r) => (
                <View
                  key={r.strategy}
                  className={selStrategy === r.strategy
                    ? styles.pillActive : styles.pill}
                  onClick={() => setSelStrategy(r.strategy)}
                >
                  <View className={styles.listTitle}>{r.label}</View>
                  <View className={styles.listDesc}>
                    得分 {r.score} · 现金 lift +{Math.round(r.roiCashLift * 100)}% · 信值占比 {Math.round(r.trustPortion * 100)}%
                  </View>
                  <View className={styles.note}>{r.note}</View>
                </View>
              ))}

              {selStrategy && (
                <View className={styles.btnPrimary} onClick={handleCreateCampaign}>
                  创建活动({selStrategy} · S5 五分钟撤销窗口)
                </View>
              )}
            </View>

            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>活动列表</View>
                <Text className={styles.sectionCount}>{campaigns.length} 条</Text>
              </View>
              {campaigns.length === 0 && (
                <View className={styles.empty}>暂无活动</View>
              )}
              {campaigns.map((c) => (
                <View key={c.campaignId} className={styles.listItem}>
                  <View className={styles.listMain}>
                    <View className={styles.listTitle}>
                      #{c.campaignId} {c.strategy}
                    </View>
                    <View className={styles.listDesc}>
                      {c.status === 'active' ? '生效中' : c.status}
                      {c.exclusive ? ' · 信值整单互斥(R2)' : ''}
                    </View>
                  </View>
                  {c.status === 'active' && (
                    <View
                      className={styles.btnSmall}
                      onClick={() => handleRevoke(c.campaignId)}
                    >
                      撤销
                    </View>
                  )}
                </View>
              ))}
            </View>
          </View>
        )}

        {/* ============ 治理 ============ */}
        {tab === 'govern' && (
          <View>
            <View className={styles.card}>
              <View className={styles.cardTitle}>合规健康度看板</View>
              {!activeShop && <View className={styles.empty}>须先激活店铺</View>}
              {activeShop && (
                <View>
                  <View className={styles.metricGrid}>
                    <View className={styles.metricItem}>
                      <View className={styles.metricValue}>
                        {health ? health.healthScore : '--'}
                      </View>
                      <View className={styles.metricLabel}>健康度</View>
                    </View>
                  </View>
                  {health?.level && (
                    <View className={styles.note}>档位: {health.level}</View>
                  )}
                </View>
              )}
            </View>

            <View className={styles.card}>
              <View className={styles.sectionHead}>
                <View className={styles.cardTitle}>经营教练贴士</View>
                <Text className={styles.sectionCount}>{tips.length} 条</Text>
              </View>
              {tips.length === 0 && (
                <View className={styles.empty}>{activeShop ? '暂无贴士' : '须先激活店铺'}</View>
              )}
              {tips.map((t, i) => (
                <View key={i} className={styles.listItem}>
                  <View className={styles.listMain}>
                    <View className={styles.listTitle}>
                      {t.kind === 'warning' ? '⚠️ ' : t.kind === 'hot_case' ? '🔥 ' : '💡 '}
                      {t.title}
                    </View>
                    <View className={styles.listDesc}>{t.body}</View>
                  </View>
                </View>
              ))}
            </View>

            {/* admin 运营区(S7 配额升降档 + 红队七向量) */}
            {isAdmin && (
              <View className={styles.card}>
                <View className={styles.cardTitle}>运营管理(admin)</View>
                {activeShop && (
                  <View>
                    <View className={styles.note}>
                      S7 配额升降档——仅生成 46号审批建议书, 惩罚性降档永不自动执行
                    </View>
                    <View className={styles.btnPrimary} onClick={() => handleQuotaAdjust('uplift')}>
                      建议升档(健康度 ≥85)
                    </View>
                    <View className={styles.btnGhost} onClick={() => handleQuotaAdjust('downgrade')}>
                      建议降档(健康度 ≤50, 经 46号审批)
                    </View>
                  </View>
                )}
                <View className={styles.btnPrimary} onClick={handleRedteam}>
                  {busy ? '仿真中…' : '红队七向量仿真(RT-01~07)'}
                </View>
                {rtResult && (
                  <View className={styles.resultCard}>
                    <View className={styles.note}>
                      红队完成: {rtResult.defended ?? rtResult.passed ?? '--'} 通过 /
                      {' '}{rtResult.total ?? rtResult.vectors?.length ?? '--'} 向量
                      {rtResult.allDefended !== undefined
                        ? ` · 防御: ${rtResult.allDefended ? '全部防住' : '存在失守'}` : ''}
                    </View>
                  </View>
                )}
              </View>
            )}

            <View className={styles.footer}>
              智能开店工作台 · S1-S8 刚性规则 · 判定链 LLM 禁入(全确定性) ·
              关店/人工兜底/巡检不受开关影响
            </View>
          </View>
        )}
      </ScrollView>
    </View>
  );
};

export default Xx65WorkbenchPage;
