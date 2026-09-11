/**
 * AI 智能入口页 · 39号六通道登录(设计文档 §2.1)
 * ============================================================
 * 「AI 识别风险 → 匹配最爽快登录方式 → 登录即达」
 *   1. 页面加载即 AI 预判(recognize): 推荐登录方式排序 + 「欢迎回来」
 *   2. 六通道一页切换: 密码/短信/扫码授权/指纹/面容/三方
 *   3. step_up 不打断层: 原位弹轻量二次验证(非跳转, 保住输入)
 *   4. 降级引导: 任一通道失败给出切换建议(扫码超时→改密码等)
 *
 * 生物红线: 前端只传摘要哈希, 原始生物数据永不上送(Mock 轨)
 */
import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import ScanCode from '@/components/ScanCode';
import { AuthAPI } from '@/api/auth';
import {
  EntryAPI, EntryRecognizeVO, LocalBioCredential,
  deriveMockAssertion, getLocalBioCredential,
} from '@/api/entry';
import { isLoggedIn } from '@/services/auth-service';

type Channel = 'password' | 'sms' | 'qr' | 'fingerprint' | 'face' | 'oauth';

/** 后端 recommendedModes 通道名 → 前端通道键 */
const MODE_TO_CHANNEL: Record<string, Channel> = {
  password: 'password', sms: 'sms', qr: 'qr',
  fingerprint: 'fingerprint', face: 'face', oauth: 'oauth',
};

const CHANNEL_META: Record<Channel, { icon: string; label: string }> = {
  password: { icon: '🔑', label: '密码' },
  sms: { icon: '💬', label: '短信' },
  qr: { icon: '📷', label: '扫码' },
  fingerprint: { icon: '🫆', label: '指纹' },
  face: { icon: '🙂', label: '面容' },
  oauth: { icon: '🔗', label: '三方' },
};

const ALL_CHANNELS: Channel[] = ['password', 'sms', 'qr', 'fingerprint', 'face', 'oauth'];

/** 生物通道文案 */
const BIO_META = {
  fingerprint: { bindLabel: '绑定本设备指纹', loginLabel: '指纹一键登录', icon: '🫆' },
  face: { bindLabel: '绑定本设备面容', loginLabel: '面容一键登录', icon: '🙂' },
} as const;

const LoginPage: React.FC = () => {
  // AI 预判
  const [recognize, setRecognize] = useState<EntryRecognizeVO | null>(null);
  // 通道与注册态
  const [channel, setChannel] = useState<Channel>('password');
  const [registerMode, setRegisterMode] = useState(false);
  // 表单
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [nickname, setNickname] = useState('');
  const [smsCode, setSmsCode] = useState('');
  const [smsCooldown, setSmsCooldown] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  // step_up 弹层(原位, 不跳转)
  const [stepUp, setStepUp] = useState<{ memberId: number; phone: string } | null>(null);
  const [stepUpCode, setStepUpCode] = useState('');
  // OAuth 绑定层
  const [oauthTicket, setOauthTicket] = useState<string | null>(null);
  // 生物凭证(本地摘要引用, 无生物明细)
  const [bioCred, setBioCred] = useState<LocalBioCredential | null>(null);
  const [deviceId, setDeviceId] = useState('');
  // 扫码授权
  const [scanVisible, setScanVisible] = useState(false);

  // ---------- AI 预判(页面加载即调) ----------
  useEffect(() => {
    (async () => {
      try {
        const r = await EntryAPI.recognize();
        setRecognize(r);
        setDeviceId(r.deviceId || '');
        // 推荐首通道生效(未知推荐回落密码)
        const first = r.recommendedModes?.[0];
        if (first && MODE_TO_CHANNEL[first]) {
          setChannel(MODE_TO_CHANNEL[first]);
        }
      } catch (_) { /* 预判失败不阻断登录(默认排序) */ }
      setBioCred(getLocalBioCredential());
    })();
  }, []);

  // 短信冷却倒计时
  useEffect(() => {
    if (smsCooldown <= 0) return;
    const t = setTimeout(() => setSmsCooldown((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [smsCooldown]);

  const validatePhone = (): boolean => {
    if (!/^1\d{10}$/.test(phone)) {
      Taro.showToast({ title: '请输入 11 位手机号', icon: 'none' });
      return false;
    }
    return true;
  };

  // ---------- 登录成功统一出口 ----------
  const onLoginSuccess = useCallback(async (nickname: string) => {
    let streakTip = '';
    try {
      const landing = await EntryAPI.landing('member');
      const streak = landing?.streak || 0;
      const pts = landing?.reward?.points || 0;
      if (streak > 0 && pts > 0) streakTip = ` · 连登${streak}天+${pts}积分`;
    } catch (_) { /* 激励 best-effort */ }
    Taro.showToast({ title: `欢迎回来, ${nickname}${streakTip}`, icon: 'none', duration: 2000 });
    setTimeout(() => Taro.navigateBack(), 1400);
  }, []);

  // ---------- 密码/短信通道(统一登录端点) ----------
  const handleUnifiedLogin = async () => {
    if (submitting) return;
    if (!validatePhone()) return;
    if (channel === 'password' && password.length < 6) {
      Taro.showToast({ title: '密码至少 6 位', icon: 'none' });
      return;
    }
    if (channel === 'sms' && smsCode.length < 4) {
      Taro.showToast({ title: '请输入短信验证码', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const res = await EntryAPI.login({
        mode: channel === 'sms' ? 'sms' : 'password',
        phone, password,
        smsCode: channel === 'sms' ? smsCode : undefined,
      });
      if (res.status === 'authenticated' && res.tokens) {
        await onLoginSuccess(res.tokens.nickname);
      } else if (res.status === 'step_up_required' && res.memberId) {
        // step_up 原位弹层(不打断上下文)
        setStepUp({ memberId: res.memberId, phone });
        setStepUpCode('');
      }
    } catch (e) {
      console.warn('[entry] 登录失败(可切换其他通道):', e);
    } finally {
      setSubmitting(false);
    }
  };

  // ---------- 注册(密码表单复用) ----------
  const handleRegister = async () => {
    if (submitting || !validatePhone()) return;
    if (password.length < 6) {
      Taro.showToast({ title: '密码至少 6 位', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      await AuthAPI.register(phone, password, nickname || undefined);
      Taro.showToast({ title: '注册成功', icon: 'success' });
      setTimeout(() => Taro.navigateBack(), 1200);
    } catch (e) {
      console.warn('[login] 注册失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // ---------- 短信发送 ----------
  const handleSendSms = async () => {
    if (smsCooldown > 0 || !validatePhone()) return;
    try {
      await EntryAPI.sendSmsCode(phone);
      setSmsCooldown(60);
      Taro.showToast({ title: '验证码已发送', icon: 'none' });
    } catch (e) {
      console.warn('[entry] 发码失败:', e);
    }
  };

  // ---------- step_up 二次验证 ----------
  const handleStepUpVerify = async () => {
    if (!stepUp || submitting) return;
    if (stepUpCode.length < 4) {
      Taro.showToast({ title: '请输入验证码', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const res = await EntryAPI.stepUpVerify({
        memberId: stepUp.memberId,
        phone: stepUp.phone,
        smsCode: stepUpCode,
      });
      if (res.status === 'authenticated' && res.tokens) {
        setStepUp(null);
        await onLoginSuccess(res.tokens.nickname);
      }
    } catch (e) {
      console.warn('[entry] step_up 失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // ---------- 扫码授权 PC 登录 ----------
  const openScan = () => {
    if (!isLoggedIn()) {
      Taro.showToast({ title: '请先登录本机, 再授权 PC', icon: 'none' });
      return;
    }
    setScanVisible(true);
  };

  const handleScanResult = async (code: string) => {
    setScanVisible(false);
    // 码值协议: ZXBJ-ENTRY:QRxxxxxxxx
    const qrId = code.startsWith('ZXBJ-ENTRY:') ? code.slice('ZXBJ-ENTRY:'.length).trim() : '';
    if (!qrId) {
      Taro.showToast({ title: '不是竹香酒登录码', icon: 'none' });
      return;
    }
    try {
      await EntryAPI.qrScan(qrId);
      await EntryAPI.qrConfirm(qrId);
      Taro.showToast({ title: '已授权该 PC 登录', icon: 'success' });
    } catch (e) {
      console.warn('[entry] 扫码授权失败(码可能过期):', e);
    }
  };

  // ---------- 生物通道(Mock 轨: 本地派生摘要, 原始数据不上送) ----------
  const handleBioLogin = async () => {
    if (submitting || !bioCred) return;
    setSubmitting(true);
    try {
      const ch = await EntryAPI.bioChallenge(bioCred.credentialId);
      const assertion = deriveMockAssertion(ch.assertionChallenge, bioCred.deviceId);
      const res = await EntryAPI.bioVerify(bioCred.credentialId, assertion);
      if (res.status === 'authenticated' && res.tokens) {
        await onLoginSuccess(res.tokens.nickname);
      } else {
        Taro.showToast({ title: '需二次核验, 请改用短信登录', icon: 'none' });
        setChannel('sms');
      }
    } catch (e) {
      console.warn('[entry] 生物登录失败(降级: 短信):', e);
      setChannel('sms');
    } finally {
      setSubmitting(false);
    }
  };

  const handleBioBind = async (bioType: 'fingerprint' | 'face') => {
    if (!isLoggedIn()) {
      Taro.showToast({ title: '请先用其他方式登录一次', icon: 'none' });
      return;
    }
    if (submitting) return;
    setSubmitting(true);
    try {
      // deviceId: AI 预判已返回(后端 hash); 兜底重取
      let did = deviceId;
      if (!did) {
        const r = await EntryAPI.recognize();
        did = r.deviceId;
        setDeviceId(did);
      }
      const enroll = await EntryAPI.bioEnroll(bioType, did);
      const cred = await EntryAPI.bioBind({
        bioType, deviceId: did, enrollChallenge: enroll.enrollChallenge,
      });
      const local: LocalBioCredential = {
        credentialId: cred.credentialId, bioType, deviceId: did,
      };
      setBioCred(local);
      Taro.showToast({ title: '绑定成功, 下次可一键登录', icon: 'success' });
    } catch (e) {
      console.warn('[entry] 生物绑定失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // ---------- OAuth 三方(Mock 轨) ----------
  const handleOauth = async (platform: 'wechat' | 'alipay' | 'qq') => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const res = await EntryAPI.oauthLogin(platform);
      if (res.status === 'loggedIn' && res.tokens) {
        await onLoginSuccess(res.tokens.nickname);
      } else if (res.ticket) {
        // 未绑定 → 绑定手机号(手机号+短信码)
        setOauthTicket(res.ticket);
        setSmsCode('');
        Taro.showToast({ title: '首次使用, 请绑定手机号', icon: 'none' });
      }
    } catch (e) {
      console.warn('[entry] 三方登录失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  const handleOauthBind = async () => {
    if (!oauthTicket || submitting) return;
    if (!validatePhone() || smsCode.length < 4) {
      Taro.showToast({ title: '请输入手机号与验证码', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      const tokens = await EntryAPI.oauthBindPhone(oauthTicket, phone, smsCode);
      setOauthTicket(null);
      await onLoginSuccess(tokens.nickname);
    } catch (e) {
      console.warn('[entry] 三方绑定失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 测试账号快捷填充
  const fillAccount = (testPhone: string, testPwd: string) => {
    setPhone(testPhone);
    setPassword(testPwd);
    Taro.showToast({ title: '已填充, 点击登录', icon: 'none' });
  };

  // 通道 chips 排序: AI 推荐(交集)优先, 其余补齐
  const channels: Channel[] = recognize
    ? (() => {
      const ranked = (recognize.recommendedModes || [])
        .map((m) => MODE_TO_CHANNEL[m])
        .filter((c): c is Channel => !!c);
      return [...ranked, ...ALL_CHANNELS.filter((c) => !ranked.includes(c))];
    })()
    : ALL_CHANNELS;
  const recommendChannel = channels[0];

  const bioType: 'fingerprint' | 'face' = channel === 'face' ? 'face' : 'fingerprint';
  const bioMeta = BIO_META[bioType];

  return (
    <View className={styles.page}>
      <NavBar title="AI 智能入口" />
      {/* 品牌头 */}
      <View className={styles.brand}>
        <View className={styles.brandIcon}>🍶</View>
        <View className={styles.brandTitle}>竹香酒</View>
        <View className={styles.brandDesc}>竹韵佳酿 · 雅致生活</View>
      </View>

      {/* AI 预判条 */}
      <View className={styles.aiBar}>
        <View className={styles.aiIcon}>✨</View>
        <View className={styles.aiText}>
          {recognize?.greeting
            ? recognize.greeting
            : 'AI 已就绪 · 为你推荐最快的登录方式'}
        </View>
      </View>

      {/* 表单卡片 */}
      <View className={styles.card}>
        {/* 六通道 chips */}
        <View className={styles.channelGrid}>
          {channels.map((c) => (
            <View
              key={c}
              className={`${styles.channelChip} ${channel === c && !registerMode ? styles.channelChipActive : ''}`}
              onClick={() => { setChannel(c); setRegisterMode(false); }}
            >
              {c === recommendChannel && <View className={styles.recommendTag}>推荐</View>}
              <View className={styles.channelIcon}>{CHANNEL_META[c].icon}</View>
              <View className={styles.channelLabel}>{CHANNEL_META[c].label}</View>
            </View>
          ))}
        </View>

        {/* 密码通道(含注册模式) */}
        {channel === 'password' && (
          <View>
            <View className={styles.formItem}>
              <Text className={styles.label}>手机号</Text>
              <View className={styles.inputWrap}>
                <Input
                  className={styles.input}
                  type='number'
                  maxlength={11}
                  value={phone}
                  placeholder='请输入 11 位手机号'
                  onInput={(e) => setPhone(e.detail.value)}
                />
              </View>
            </View>
            {registerMode && (
              <View className={styles.formItem}>
                <Text className={styles.label}>昵称(选填)</Text>
                <View className={styles.inputWrap}>
                  <Input
                    className={styles.input}
                    type='text'
                    maxlength={20}
                    value={nickname}
                    placeholder='给自己起个名字'
                    onInput={(e) => setNickname(e.detail.value)}
                  />
                </View>
              </View>
            )}
            <View className={styles.formItem}>
              <Text className={styles.label}>密码</Text>
              <View className={styles.inputWrap}>
                <Input
                  className={styles.input}
                  type='text'
                  password
                  maxlength={64}
                  value={password}
                  placeholder={registerMode ? '设置密码(至少 6 位)' : '请输入密码'}
                  onInput={(e) => setPassword(e.detail.value)}
                />
              </View>
            </View>
            <View
              className={styles.submitBtn}
              onClick={registerMode ? handleRegister : handleUnifiedLogin}
            >
              {submitting
                ? (registerMode ? '注册中...' : '登录中...')
                : (registerMode ? '注册并登录' : '登 录')}
            </View>
            <View className={styles.tip} onClick={() => setRegisterMode((m) => !m)}>
              {registerMode ? '已有账号? 点此返回登录' : '还没有账号? 立即注册'}
            </View>
            {!registerMode && (
              <View className={styles.testAccounts}>
                <View className={styles.testAccountsTitle}>体验账号(点击快捷填充)</View>
                <View className={styles.testAccountRow} onClick={() => fillAccount('13800000001', 'test123456')}>
                  <View className={styles.testAccountName}>👤 普通会员</View>
                  <View className={styles.testAccountPhone}>13800000001</View>
                </View>
                <View className={styles.testAccountRow} onClick={() => fillAccount('13800000002', 'test123456')}>
                  <View className={styles.testAccountName}>👑 站点管理员</View>
                  <View className={styles.testAccountPhone}>13800000002</View>
                </View>
              </View>
            )}
          </View>
        )}

        {/* 短信通道 */}
        {channel === 'sms' && (
          <View>
            <View className={styles.formItem}>
              <Text className={styles.label}>手机号</Text>
              <View className={styles.inputWrap}>
                <Input
                  className={styles.input}
                  type='number'
                  maxlength={11}
                  value={phone}
                  placeholder='请输入 11 位手机号'
                  onInput={(e) => setPhone(e.detail.value)}
                />
              </View>
            </View>
            <View className={styles.formItem}>
              <Text className={styles.label}>验证码</Text>
              <View className={styles.codeRow}>
                <View className={styles.inputWrap}>
                  <Input
                    className={styles.input}
                    type='number'
                    maxlength={6}
                    value={smsCode}
                    placeholder='短信验证码'
                    onInput={(e) => setSmsCode(e.detail.value)}
                  />
                </View>
                <View
                  className={`${styles.sendBtn} ${smsCooldown > 0 ? styles.sendBtnDisabled : ''}`}
                  onClick={handleSendSms}
                >
                  {smsCooldown > 0 ? `${smsCooldown}s` : '发送'}
                </View>
              </View>
            </View>
            <View className={styles.submitBtn} onClick={handleUnifiedLogin}>
              {submitting ? '登录中...' : '登 录'}
            </View>
            <View className={styles.tip}>收不到? 可切换密码或三方登录</View>
          </View>
        )}

        {/* 扫码通道(本机为确认方, 授权 PC 登录) */}
        {channel === 'qr' && (
          <View className={styles.qrPanel}>
            <View className={styles.qrIcon}>🖥️</View>
            <View className={styles.qrTitle}>扫一扫, 授权 PC 登录</View>
            <View className={styles.qrDesc}>
              PC 端打开竹香酒登录页展示二维码, 本机扫码确认后 PC 自动登录(须本机已登录)
            </View>
            <View className={styles.submitBtn} onClick={openScan}>📷 扫描登录码</View>
            <View className={styles.tip}>码 3 分钟内有效 · 也可在 PC 端改用密码登录</View>
          </View>
        )}

        {/* 指纹/面容通道(Mock 轨: 摘要哈希, 原始数据不上送) */}
        {(channel === 'fingerprint' || channel === 'face') && (
          <View className={styles.bioPanel}>
            {bioCred && bioCred.bioType === bioType ? (
              <View>
                <View className={styles.bioIcon}>{bioMeta.icon}</View>
                <View className={styles.qrTitle}>{bioMeta.loginLabel}</View>
                <View className={styles.qrDesc}>设备本地验证 · 摘要比对 · 60 秒内完成</View>
                <View className={styles.submitBtn} onClick={handleBioLogin}>
                  {submitting ? '验证中...' : bioMeta.loginLabel}
                </View>
              </View>
            ) : (
              <View>
                <View className={styles.bioIcon}>{bioMeta.icon}</View>
                <View className={styles.qrTitle}>{bioMeta.bindLabel}</View>
                <View className={styles.qrDesc}>
                  {isLoggedIn()
                    ? '绑定后本设备可一键登录(30 天内免密)'
                    : '先用其他方式登录一次, 再来绑定即可一键登录'}
                </View>
                <View className={styles.submitBtn} onClick={() => handleBioBind(bioType)}>
                  {submitting ? '绑定中...' : bioMeta.bindLabel}
                </View>
              </View>
            )}
            <View className={styles.tip}>生物数据仅存设备端摘要, 永不上传原始信息</View>
          </View>
        )}

        {/* 三方通道(Mock 轨: 平台未接入, code 确定性派生) */}
        {channel === 'oauth' && (
          <View className={styles.oauthPanel}>
            <View
              className={styles.oauthBtn}
              onClick={() => handleOauth('wechat')}
            >
              <View className={styles.oauthIcon}>💚</View>
              <View>微信一键登录</View>
            </View>
            <View
              className={styles.oauthBtn}
              onClick={() => handleOauth('alipay')}
            >
              <View className={styles.oauthIcon}>💙</View>
              <View>支付宝登录</View>
            </View>
            <View
              className={styles.oauthBtn}
              onClick={() => handleOauth('qq')}
            >
              <View className={styles.oauthIcon}>🐧</View>
              <View>QQ 登录</View>
            </View>
            <View className={styles.tip}>首次使用需绑定手机号(短信核验)</View>
          </View>
        )}
      </View>

      {/* step_up 原位弹层(不打断上下文) */}
      {stepUp && (
        <View className={styles.mask}>
          <View className={styles.stepUpPanel}>
            <View className={styles.stepUpTitle}>🔒 需要短信二次核验</View>
            <View className={styles.stepUpDesc}>
              AI 风控判定本次登录需轻量二次验证, 一次通过即可
            </View>
            <View className={styles.formItem}>
              <Text className={styles.label}>手机号</Text>
              <View className={styles.inputWrap}>
                <Input
                  className={styles.input}
                  type='number'
                  maxlength={11}
                  value={stepUp.phone}
                  disabled
                />
              </View>
            </View>
            <View className={styles.formItem}>
              <Text className={styles.label}>验证码</Text>
              <View className={styles.codeRow}>
                <View className={styles.inputWrap}>
                  <Input
                    className={styles.input}
                    type='number'
                    maxlength={6}
                    value={stepUpCode}
                    placeholder='短信验证码'
                    onInput={(e) => setStepUpCode(e.detail.value)}
                  />
                </View>
                <View
                  className={`${styles.sendBtn} ${smsCooldown > 0 ? styles.sendBtnDisabled : ''}`}
                  onClick={async () => {
                    if (smsCooldown <= 0) {
                      try {
                        await EntryAPI.sendSmsCode(stepUp.phone);
                        setSmsCooldown(60);
                        Taro.showToast({ title: '验证码已发送', icon: 'none' });
                      } catch (_) { /* 频控提示由 request 层处理 */ }
                    }
                  }}
                >
                  {smsCooldown > 0 ? `${smsCooldown}s` : '发送'}
                </View>
              </View>
            </View>
            <View className={styles.submitBtn} onClick={handleStepUpVerify}>
              {submitting ? '验证中...' : '完成验证'}
            </View>
            <View className={styles.stepUpCancel} onClick={() => setStepUp(null)}>取消</View>
          </View>
        </View>
      )}

      {/* OAuth 绑定弹层 */}
      {oauthTicket && (
        <View className={styles.mask}>
          <View className={styles.stepUpPanel}>
            <View className={styles.stepUpTitle}>🔗 绑定手机号</View>
            <View className={styles.stepUpDesc}>首次三方登录, 绑定后下次一键直达</View>
            <View className={styles.formItem}>
              <Text className={styles.label}>手机号</Text>
              <View className={styles.inputWrap}>
                <Input
                  className={styles.input}
                  type='number'
                  maxlength={11}
                  value={phone}
                  placeholder='请输入 11 位手机号'
                  onInput={(e) => setPhone(e.detail.value)}
                />
              </View>
            </View>
            <View className={styles.formItem}>
              <Text className={styles.label}>验证码</Text>
              <View className={styles.codeRow}>
                <View className={styles.inputWrap}>
                  <Input
                    className={styles.input}
                    type='number'
                    maxlength={6}
                    value={smsCode}
                    placeholder='短信验证码'
                    onInput={(e) => setSmsCode(e.detail.value)}
                  />
                </View>
                <View
                  className={`${styles.sendBtn} ${smsCooldown > 0 ? styles.sendBtnDisabled : ''}`}
                  onClick={handleSendSms}
                >
                  {smsCooldown > 0 ? `${smsCooldown}s` : '发送'}
                </View>
              </View>
            </View>
            <View className={styles.submitBtn} onClick={handleOauthBind}>
              {submitting ? '绑定中...' : '绑定并登录'}
            </View>
            <View className={styles.stepUpCancel} onClick={() => setOauthTicket(null)}>取消</View>
          </View>
        </View>
      )}

      {/* 扫码授权(复用 ScanCode 双模式组件) */}
      <ScanCode
        visible={scanVisible}
        onClose={() => setScanVisible(false)}
        onResult={handleScanResult}
        hint='对准 PC 端展示的登录二维码'
      />
    </View>
  );
};

export default LoginPage;
