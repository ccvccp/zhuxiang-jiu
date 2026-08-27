/**
 * 主题智能管理 · 权限管控的颜色/图标管理后台
 * 数据来源: 后端 /api/site-theme/*
 * 权限: 仅 role=admin 会员可操作(双层权限: 小程序角色门控 + 后端 JWT 鉴权)
 * 功能: 新建/编辑主题(预设模板+自定义颜色+金刚区图标) / AI 体检 / 激活 / 回滚 / AI 推荐
 */
import React, { useState, useEffect } from 'react';
import { View, Text, Input, Textarea, Image } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import { SiteThemeAPI, ThemeVO, ThemeColorsVO, ThemeAiCheckVO, ThemeLogVO, IconItemVO } from '@/api/siteTheme';
import { MemberAPI } from '@/api/member';
import { applyActiveTheme } from '@/services/theme-service';
import { getSession, setSession } from '@/services/auth-service';

/** 图标值是否为图片(data URL / http) */
const isImageIcon = (v: string): boolean =>
  typeof v === 'string' && (v.startsWith('data:image/') || v.startsWith('http'));

const STATUS_NAME: Record<string, string> = {
  draft: '草稿',
  active: '激活中',
  archived: '已归档',
};

const ACTION_NAME: Record<string, string> = {
  create: '创建', update: '编辑', activate: '激活',
  deactivate: '解除激活', archive: '归档', rollback: '回滚',
};

// 预设模板(与后端 PRESET_THEMES 一致)
const PRESET_TEMPLATES: Array<{ name: string; colors: ThemeColorsVO }> = [
  { name: '竹绿经典', colors: { primary: '#355c44', primaryLight: '#4a7c59', navBar: '#355c44', tabSelected: '#355c44', tabColor: '#999999', tabBg: '#ffffff', textOnPrimary: '#ffffff' } },
  { name: '新春红金', colors: { primary: '#b03a2e', primaryLight: '#d35f52', navBar: '#b03a2e', tabSelected: '#b03a2e', tabColor: '#999999', tabBg: '#ffffff', textOnPrimary: '#ffffff' } },
  { name: '中秋金棕', colors: { primary: '#8c6a3f', primaryLight: '#b08d5f', navBar: '#8c6a3f', tabSelected: '#8c6a3f', tabColor: '#999999', tabBg: '#ffffff', textOnPrimary: '#ffffff' } },
  { name: '夏日竹青', colors: { primary: '#2e7d6b', primaryLight: '#4a9c88', navBar: '#2e7d6b', tabSelected: '#2e7d6b', tabColor: '#999999', tabBg: '#ffffff', textOnPrimary: '#ffffff' } },
  { name: '国庆中国红', colors: { primary: '#a93226', primaryLight: '#cd5c5c', navBar: '#a93226', tabSelected: '#a93226', tabColor: '#999999', tabBg: '#ffffff', textOnPrimary: '#ffffff' } },
];

// 主色候选色板(快速选择)
const COLOR_SWATCHES = [
  '#355c44', '#2e7d6b', '#4a9c88', '#b03a2e', '#a93226', '#8c6a3f',
  '#2c3e50', '#6a4c93', '#c0392b', '#d4a017', '#16a085', '#34495e',
];

// 金刚区图标配置(key 与首页 QUICK_ENTRIES 一致)
const QUICK_GRID_ITEMS: Array<{ key: string; label: string; fallback: string; candidates: string[] }> = [
  { key: 'signin', label: '每日签到', fallback: '✅', candidates: ['✅', '📅', '🎯', '⭐', '🖊️'] },
  { key: 'groupbuy', label: '组团团购', fallback: '🛒', candidates: ['🛒', '🛍️', '🎁', '🧺', '🏷️'] },
  { key: 'recharge', label: '余额赚钱', fallback: '💰', candidates: ['💰', '💵', '🪙', '💳', '📈'] },
  { key: 'activity', label: '活动中心', fallback: '🎁', candidates: ['🎁', '🎉', '🎊', '🏆', '🎪'] },
  { key: 'promotion', label: '扫码赚钱', fallback: '🤝', candidates: ['🤝', '📱', '🔗', '👥', '📢'] },
  { key: 'pocket', label: '顺手赚钱', fallback: '🤲', candidates: ['🤲', '✋', '💪', '🧧', '🖐️'] },
  { key: 'member', label: '会员权益', fallback: '👑', candidates: ['👑', '💎', '⭐', '🎖️', '🏅'] },
  { key: 'orders', label: '我的订单', fallback: '📦', candidates: ['📦', '📋', '🧾', '🚚', '📮'] },
  { key: 'service', label: '在线客服', fallback: '🎧', candidates: ['🎧', '💬', '📞', '🛎️', '🙋'] },
];

// 编辑器表单状态
interface EditorState {
  themeId: number;          // 0=新建
  name: string;
  description: string;
  colors: ThemeColorsVO;
  quickGrid: Record<string, string>;
}

const emptyEditor = (): EditorState => ({
  themeId: 0,
  name: '',
  description: '',
  colors: { ...PRESET_TEMPLATES[0].colors },
  quickGrid: {},
});

const ThemeAdminPage: React.FC = () => {
  const [role, setRole] = useState<string>('');
  const [themes, setThemes] = useState<ThemeVO[]>([]);
  const [logs, setLogs] = useState<ThemeLogVO[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  // AI 体检结果弹层
  const [checkResult, setCheckResult] = useState<ThemeAiCheckVO | null>(null);
  // 主题编辑器弹层
  const [editor, setEditor] = useState<EditorState | null>(null);
  // 图标资源库(后端拉取, 选择器展示)
  const [iconLibrary, setIconLibrary] = useState<IconItemVO[]>([]);
  // 图标选择器(当前正在更换的金刚区 key, null=关闭)
  const [iconPickerKey, setIconPickerKey] = useState<string | null>(null);
  // 自定义 emoji 输入
  const [customIcon, setCustomIcon] = useState('');
  // 图片上传中
  const [uploading, setUploading] = useState(false);

  const loadData = async () => {
    try {
      const [list, logList] = await Promise.all([
        SiteThemeAPI.list().catch((): ThemeVO[] => []),
        SiteThemeAPI.logs(10).catch((): ThemeLogVO[] => []),
      ]);
      setThemes(list);
      setLogs(logList);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    (async () => {
      // 权限门控: 读取会员角色
      const member = await MemberAPI.profile().catch(() => null);
      const r = (member as any)?.role || 'member';
      setRole(r);
      // 回填会话角色(旧会话无 role 字段时补齐, 供管理端请求头使用)
      const session = getSession();
      if (r === 'admin' && session && session.role !== 'admin') {
        setSession({ ...session, role: 'admin' });
      }
      // 图标资源库(失败降级为内置候选并集)
      const lib = await SiteThemeAPI.icons().catch(() => [] as IconItemVO[]);
      if (lib.length > 0) {
        setIconLibrary(lib);
      } else {
        const builtin = new Set<string>();
        QUICK_GRID_ITEMS.forEach(i => i.candidates.forEach(c => builtin.add(c)));
        setIconLibrary([...builtin].map((emoji, idx) => ({
          iconId: -(idx + 1), name: `builtin_${idx}`, emoji, url: '',
        })));
      }
      loadData();
    })();
  }, []);

  // AI 健康度体检
  const handleAiCheck = async (theme: ThemeVO) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await SiteThemeAPI.aiCheck(theme.themeId);
      setCheckResult(r);
      loadData();
    } catch (e) {
      console.warn('[theme-admin] AI 体检失败:', e);
    } finally {
      setBusy(false);
    }
  };

  // 激活主题(后端 AI<60 拒绝)
  const handleActivate = (theme: ThemeVO) => {
    if (busy) return;
    Taro.showModal({
      title: '激活主题',
      content: `激活「${theme.name}」后 C 端导航栏/tabBar 即时生效, 确认?`,
      success: async r => {
        if (!r.confirm) return;
        setBusy(true);
        try {
          await SiteThemeAPI.activate(theme.themeId);
          Taro.showToast({ title: '已激活, 即时生效', icon: 'success' });
          await applyActiveTheme();  // 本机立即换肤
          loadData();
        } catch (e) {
          console.warn('[theme-admin] 激活失败:', e);
        } finally {
          setBusy(false);
        }
      },
    });
  };

  // 一键回滚
  const handleRollback = (log: ThemeLogVO) => {
    if (busy) return;
    Taro.showModal({
      title: '一键回滚',
      content: `将主题 #${log.themeId} 回滚到「${ACTION_NAME[log.action] || log.action}」之前的状态, 确认?`,
      success: async r => {
        if (!r.confirm) return;
        setBusy(true);
        try {
          await SiteThemeAPI.rollback(log.logId);
          Taro.showToast({ title: '已回滚', icon: 'success' });
          await applyActiveTheme();
          loadData();
        } catch (e) {
          console.warn('[theme-admin] 回滚失败:', e);
        } finally {
          setBusy(false);
        }
      },
    });
  };

  // AI 季节推荐
  const handleRecommend = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await SiteThemeAPI.recommend();
      const best = r.recommendations?.[0];
      if (best) {
        Taro.showModal({
          title: `AI 推荐 · ${best.name}`,
          content: `${best.reasons.join('; ')}\n推荐分 ${best.recommendScore}`,
          showCancel: false,
        });
      }
    } catch (e) {
      console.warn('[theme-admin] 推荐失败:', e);
    } finally {
      setBusy(false);
    }
  };

  // 打开编辑器: 新建
  const openCreate = () => {
    setEditor(emptyEditor());
  };

  // 打开编辑器: 编辑草稿
  const openEdit = (theme: ThemeVO) => {
    setEditor({
      themeId: theme.themeId,
      name: theme.name,
      description: theme.description || '',
      colors: { ...theme.colors } as ThemeColorsVO,
      quickGrid: { ...((theme.icons || {}).quickGrid || {}) },
    });
  };

  // 应用预设模板
  const applyTemplate = (tpl: { name: string; colors: ThemeColorsVO }) => {
    if (!editor) return;
    setEditor({
      ...editor,
      name: editor.name || tpl.name,
      colors: { ...tpl.colors },
    });
  };

  // 应用选中的图标到当前图标位(emoji/图片url 或空串=还原默认)
  const applyIcon = (value: string) => {
    if (!editor || !iconPickerKey) return;
    const grid = { ...editor.quickGrid };
    if (value) {
      grid[iconPickerKey] = value;
    } else {
      delete grid[iconPickerKey];
    }
    setEditor({ ...editor, quickGrid: grid });
    setIconPickerKey(null);
    setCustomIcon('');
  };

  // 从相册/拍照选图 → 读 base64 → 上传图标库(applyTo: null=仅入库, 有值=应用到图标位)
  const handleUploadIcon = (applyTo: string | null) => {
    if (uploading) return;
    Taro.chooseImage({
      count: 1,
      sizeType: ['compressed'],
      sourceType: ['album', 'camera'],
      success: res => {
        const path = res.tempFilePaths?.[0];
        if (!path) return;
        setUploading(true);
        const fsm = Taro.getFileSystemManager();
        fsm.readFile({
          filePath: path,
          encoding: 'base64',
          success: async readRes => {
            try {
              const ext = (path.split('.').pop() || 'png').toLowerCase();
              const mime = ext === 'jpg' ? 'jpeg' : ext;
              const dataUrl = `data:image/${mime};base64,${readRes.data}`;
              const icon = await SiteThemeAPI.uploadIcon(dataUrl);
              setIconLibrary(prev => [...prev, icon]);
              Taro.showToast({ title: '上传成功', icon: 'success' });
              // 应用到指定图标位(编辑器内触发时)
              if (applyTo && editor) {
                const grid = { ...editor.quickGrid };
                grid[applyTo] = icon.url;
                setEditor({ ...editor, quickGrid: grid });
                setIconPickerKey(null);
              }
            } catch (e) {
              console.warn('[theme-admin] 图标上传失败:', e);
            } finally {
              setUploading(false);
            }
          },
          fail: () => {
            setUploading(false);
            Taro.showToast({ title: '读取图片失败', icon: 'none' });
          },
        });
      },
    });
  };

  // 点击图标库条目 → 复制图标值(图片 URL / emoji)
  const handleCopyIcon = (ic: IconItemVO) => {
    const value = ic.url || ic.emoji;
    Taro.setClipboardData({
      data: value,
      success: () => Taro.showToast({ title: '已复制, 可粘贴到自定义输入', icon: 'none' }),
    });
  };

  // 保存(新建或编辑)
  const handleSaveEditor = async () => {
    if (!editor || busy) return;
    if (!editor.name.trim()) {
      Taro.showToast({ title: '请输入主题名称', icon: 'none' });
      return;
    }
    setBusy(true);
    try {
      const icons = { quickGrid: editor.quickGrid };
      if (editor.themeId === 0) {
        await SiteThemeAPI.create(editor.name.trim(), editor.colors, icons, editor.description);
        Taro.showToast({ title: '主题已创建', icon: 'success' });
      } else {
        await SiteThemeAPI.update(editor.themeId, {
          name: editor.name.trim(),
          colors: editor.colors,
          icons,
          description: editor.description,
        });
        Taro.showToast({ title: '主题已保存', icon: 'success' });
      }
      setEditor(null);
      loadData();
    } catch (e) {
      console.warn('[theme-admin] 保存失败:', e);
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <View className={styles.page}>
        <View className={styles.loading}>加载中...</View>
      </View>
    );
  }

  // 权限门控: 非管理员显示无权限占位
  if (role !== 'admin') {
    return (
      <View className={styles.page}>
        <View className={styles.noPerm}>
          <View className={styles.noPermIcon}>🔒</View>
          <View className={styles.noPermTitle}>无管理权限</View>
          <View className={styles.noPermDesc}>
            网站颜色与图标管理仅对管理员开放{'\n'}如需变更请联系站点管理员
          </View>
        </View>
      </View>
    );
  }

  return (
    <View className={styles.page}>
      {/* 标题栏 */}
      <View className={styles.header}>
        <View>
          <View className={styles.headerTitle}>主题智能管理</View>
          <View className={styles.headerDesc}>颜色 / 图标 · 权限管控 · AI 把关</View>
        </View>
        <View className={styles.recommendBtn} onClick={handleRecommend}>
          🤖 AI 推荐
        </View>
      </View>

      {/* 主题列表 */}
      <View className={styles.section}>
        <View className={styles.sectionHeaderRow}>
          <View className={styles.sectionTitle}>主题方案({themes.length})</View>
          <View className={styles.createBtn} onClick={openCreate}>＋ 新建主题</View>
        </View>
        {themes.map(t => (
          <View key={t.themeId} className={`${styles.themeCard} ${t.status === 'active' ? styles.themeActive : ''}`}>
            <View className={styles.themeTop}>
              <View className={styles.themeName}>{t.name}</View>
              <View className={`${styles.statusTag} ${t.status === 'active' ? styles.statusActive : ''}`}>
                {STATUS_NAME[t.status] || t.status}
              </View>
            </View>
            {t.description ? (
              <View className={styles.themeDesc}>{t.description}</View>
            ) : null}
            {/* 配色预览 */}
            <View className={styles.colorRow}>
              <View className={styles.colorChip} style={{ background: t.colors?.primary }}>
                <Text style={{ color: '#fff', fontSize: '18rpx' }}>主色</Text>
              </View>
              <View className={styles.colorChip} style={{ background: t.colors?.primaryLight }} />
              <View className={styles.colorChip} style={{ background: t.colors?.navBar }} />
              <View className={styles.colorChip} style={{ background: t.colors?.tabSelected }} />
              <View className={styles.colorChip} style={{ background: t.colors?.tabBg, border: '1px solid #e5e6eb' }} />
              <View className={styles.aiBadge}>AI {t.aiScoreLatest || '--'}</View>
            </View>
            {/* 图标覆盖预览 */}
            {t.icons?.quickGrid && Object.keys(t.icons.quickGrid).length > 0 && (
              <View className={styles.iconPreviewRow}>
                {Object.entries(t.icons.quickGrid).slice(0, 6).map(([k, v]) => {
                  const val = String(v);
                  return isImageIcon(val) ? (
                    <Image key={k} src={val} className={styles.iconPreviewImg} mode='aspectFit' />
                  ) : (
                    <View key={k} className={styles.iconPreviewChip}>{val}</View>
                  );
                })}
                <View className={styles.iconPreviewMore}>图标已自定义</View>
              </View>
            )}
            {/* 操作 */}
            <View className={styles.themeActions}>
              <View className={styles.opBtn} onClick={() => handleAiCheck(t)}>AI 体检</View>
              {t.status === 'draft' && (
                <View className={styles.opBtn} onClick={() => openEdit(t)}>编辑</View>
              )}
              {t.status !== 'active' && (
                <View className={`${styles.opBtn} ${styles.opPrimary}`} onClick={() => handleActivate(t)}>激活</View>
              )}
            </View>
          </View>
        ))}
      </View>

      {/* 图标资源库管理(独立入口) */}
      <View className={styles.section}>
        <View className={styles.sectionHeaderRow}>
          <View className={styles.sectionTitle}>图标管理({iconLibrary.length})</View>
          <View className={styles.createBtn} onClick={() => handleUploadIcon(null)}>
            {uploading ? '上传中...' : '＋ 上传图标'}
          </View>
        </View>
        <View className={styles.iconLibDesc}>
          上传 png/jpg/webp 图片(≤200KB)入库; 编辑主题时在图标选择器中选用
        </View>
        <View className={styles.pickerGrid}>
          {iconLibrary.map(ic => (
            <View key={ic.iconId} className={styles.pickerCell} onClick={() => handleCopyIcon(ic)}>
              {ic.url ? (
                <Image src={ic.url} className={styles.pickerImg} mode='aspectFit' />
              ) : (
                ic.emoji
              )}
            </View>
          ))}
        </View>
      </View>

      {/* 变更审计 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>变更审计(支持回滚)</View>
        {logs.length === 0 && <View className={styles.empty}>暂无变更记录</View>}
        {logs.map(l => (
          <View key={l.logId} className={styles.logItem}>
            <View className={styles.logLeft}>
              <View className={styles.logAction}>{ACTION_NAME[l.action] || l.action}</View>
              <View className={styles.logMeta}>
                主题#{l.themeId} · 管理员{l.adminId} · {(l.createdAt || '').slice(0, 16).replace('T', ' ')}
              </View>
            </View>
            {l.action !== 'create' && (
              <View className={styles.rollbackBtn} onClick={() => handleRollback(l)}>回滚</View>
            )}
          </View>
        ))}
      </View>

      {/* 说明 */}
      <View className={styles.section}>
        <View className={styles.sectionTitle}>管理说明</View>
        <View className={styles.rulesCard}>
          <View className={styles.ruleItem}>1. 双层权限: 小程序管理员角色 + 后端 JWT 令牌鉴权</View>
          <View className={styles.ruleItem}>2. 新建/编辑主题: 选预设模板 → 调颜色/图标 → AI 体检 → 激活</View>
          <View className={styles.ruleItem}>3. 激活前 AI 健康度体检(对比度/和谐度/品牌基因) &lt;60 分拒绝</View>
          <View className={styles.ruleItem}>4. 激活后 C 端导航栏/tabBar/金刚区图标免发版即时生效</View>
          <View className={styles.ruleItem}>5. 全程审计留痕, 误操作可一键回滚</View>
        </View>
      </View>

      {/* 主题编辑器弹层 */}
      {editor && (
        <View className={styles.mask} onClick={() => setEditor(null)}>
          <View className={styles.sheet} onClick={e => e.stopPropagation()}>
            <View className={styles.sheetTitle}>
              {editor.themeId === 0 ? '新建主题' : `编辑主题 #${editor.themeId}`}
            </View>

            {/* 预设模板 */}
            <View className={styles.editorLabel}>① 选预设模板</View>
            <View className={styles.tplRow}>
              {PRESET_TEMPLATES.map(tpl => (
                <View
                  key={tpl.name}
                  className={`${styles.tplChip} ${editor.colors.primary === tpl.colors.primary ? styles.tplChipActive : ''}`}
                  onClick={() => applyTemplate(tpl)}
                >
                  <View className={styles.tplDot} style={{ background: tpl.colors.primary }} />
                  <View className={styles.tplName}>{tpl.name}</View>
                </View>
              ))}
            </View>

            {/* 名称 */}
            <View className={styles.editorLabel}>② 主题名称</View>
            <Input
              className={styles.input}
              value={editor.name}
              maxlength={30}
              placeholder='如: 中秋金棕限定'
              onInput={e => setEditor({ ...editor, name: e.detail.value })}
            />

            {/* 主色 */}
            <View className={styles.editorLabel}>③ 主色调(点击色板快捷选择)</View>
            <View className={styles.swatchRow}>
              {COLOR_SWATCHES.map(c => (
                <View
                  key={c}
                  className={`${styles.swatch} ${editor.colors.primary === c ? styles.swatchActive : ''}`}
                  style={{ background: c }}
                  onClick={() => setEditor({
                    ...editor,
                    colors: {
                      ...editor.colors,
                      primary: c,
                      primaryLight: c,
                      navBar: c,
                      tabSelected: c,
                    },
                  })}
                />
              ))}
            </View>

            {/* 金刚区图标 */}
            <View className={styles.editorLabel}>④ 金刚区图标(点击图标位打开选择器更换)</View>
            <View className={styles.gridIconList}>
              {QUICK_GRID_ITEMS.map(item => {
                const current = editor.quickGrid[item.key] || item.fallback;
                const overridden = !!editor.quickGrid[item.key];
                return (
                  <View key={item.key} className={styles.gridIconItem}>
                    <View className={styles.gridIconTop}>
                      <View
                        className={`${styles.gridIconEmoji} ${overridden ? styles.gridIconOverridden : ''}`}
                        onClick={() => {
                          setIconPickerKey(item.key);
                          setCustomIcon('');
                        }}
                      >
                        {isImageIcon(current) ? (
                          <Image src={current} className={styles.gridIconImg} mode='aspectFit' />
                        ) : (
                          current
                        )}
                      </View>
                    </View>
                    <View className={styles.gridIconLabel}>{item.label}</View>
                    <View className={styles.gridIconHint}>
                      {overridden ? '已自定义' : '默认'}
                    </View>
                  </View>
                );
              })}
            </View>

            {/* 描述 */}
            <View className={styles.editorLabel}>⑤ 描述(可选)</View>
            <Textarea
              className={styles.textarea}
              value={editor.description}
              maxlength={200}
              placeholder='主题说明, 如适用节日/设计意图'
              onInput={e => setEditor({ ...editor, description: e.detail.value })}
            />

            {/* 保存 */}
            <View className={styles.submitBtn} onClick={handleSaveEditor}>
              {busy ? '保存中...' : '保存主题'}
            </View>
          </View>
        </View>
      )}

      {/* 图标选择器弹层(点击图标位触发) */}
      {editor && iconPickerKey && (
        <View className={styles.mask} onClick={() => setIconPickerKey(null)}>
          <View className={styles.sheet} onClick={e => e.stopPropagation()}>
            {(() => {
              const item = QUICK_GRID_ITEMS.find(i => i.key === iconPickerKey)!;
              const current = editor.quickGrid[item.key] || item.fallback;
              return (
                <>
                  <View className={styles.sheetTitle}>
                    更换图标 · {item.label}
                    <Text className={styles.pickerCurrent}> 当前 {current}</Text>
                  </View>

                  {/* 推荐(候选) */}
                  <View className={styles.editorLabel}>推荐图标</View>
                  <View className={styles.pickerGrid}>
                    {item.candidates.map(c => (
                      <View
                        key={c}
                        className={`${styles.pickerCell} ${c === current ? styles.pickerCellActive : ''}`}
                        onClick={() => applyIcon(c)}
                      >
                        {c}
                      </View>
                    ))}
                  </View>

                  {/* 图标资源库 */}
                  <View className={styles.editorLabel}>
                    图标库({iconLibrary.length} 个, 含上传图片)
                  </View>
                  <View className={styles.pickerGrid}>
                    {iconLibrary.map(ic => {
                      const value = ic.url || ic.emoji;
                      return (
                        <View
                          key={ic.iconId}
                          className={`${styles.pickerCell} ${value === current ? styles.pickerCellActive : ''}`}
                          onClick={() => applyIcon(value)}
                        >
                          {ic.url ? (
                            <Image src={ic.url} className={styles.pickerImg} mode='aspectFit' />
                          ) : (
                            ic.emoji
                          )}
                        </View>
                      );
                    })}
                  </View>

                  {/* 上传图片图标 */}
                  <View className={styles.editorLabel}>上传图片图标(png/jpg/webp, ≤200KB)</View>
                  <View className={styles.uploadBtn} onClick={() => handleUploadIcon(iconPickerKey)}>
                    {uploading ? '上传中...' : '📷 从相册/拍照选择'}
                  </View>

                  {/* 自定义输入 */}
                  <View className={styles.editorLabel}>自定义(输入任意 emoji/字符)</View>
                  <View className={styles.customRow}>
                    <Input
                      className={styles.customInput}
                      value={customIcon}
                      maxlength={4}
                      placeholder='粘贴或输入 emoji'
                      onInput={e => setCustomIcon(e.detail.value)}
                    />
                    <View
                      className={styles.customApplyBtn}
                      onClick={() => {
                        if (!customIcon.trim()) {
                          Taro.showToast({ title: '请先输入', icon: 'none' });
                          return;
                        }
                        applyIcon(customIcon.trim());
                      }}
                    >
                      应用
                    </View>
                  </View>

                  {/* 操作 */}
                  <View className={styles.pickerActions}>
                    <View className={styles.pickerResetBtn} onClick={() => applyIcon('')}>
                      还原默认({item.fallback})
                    </View>
                    <View className={styles.submitBtn} onClick={() => setIconPickerKey(null)}>完成</View>
                  </View>
                </>
              );
            })()}
          </View>
        </View>
      )}

      {/* AI 体检结果弹层 */}
      {checkResult && (
        <View className={styles.mask} onClick={() => setCheckResult(null)}>
          <View className={styles.sheet} onClick={e => e.stopPropagation()}>
            <View className={styles.sheetTitle}>
              AI 健康度 · {checkResult.score} 分
              <Text className={checkResult.passed ? styles.passTag : styles.failTag}>
                {checkResult.passed ? ' 通过' : ' 未通过'}
              </Text>
            </View>
            {checkResult.factors.map(f => (
              <View key={f.name} className={styles.factorItem}>
                <View className={styles.factorRow}>
                  <Text className={styles.factorLabel}>{f.label}</Text>
                  <Text className={styles.factorScore}>{f.score}/{f.maxScore}</Text>
                </View>
                <View className={styles.factorBar}>
                  <View
                    className={styles.factorFill}
                    style={{ width: `${(f.score / f.maxScore) * 100}%` }}
                  />
                </View>
                <View className={styles.factorDetail}>{f.detail}</View>
              </View>
            ))}
            <View className={styles.submitBtn} onClick={() => setCheckResult(null)}>知道了</View>
          </View>
        </View>
      )}
    </View>
  );
};

export default ThemeAdminPage;
