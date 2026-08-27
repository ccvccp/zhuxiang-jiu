/**
 * 登录/注册页 · 手机号密码登录 + 注册(自动登录)
 * 登录成功 → 保存会话 → 返回上一页
 */
import React, { useState } from 'react';
import { View, Text, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import { AuthAPI } from '@/api/auth';

type Mode = 'login' | 'register';

const LoginPage: React.FC = () => {
  const [mode, setMode] = useState<Mode>('login');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [nickname, setNickname] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const validate = (): boolean => {
    if (!/^1\d{10}$/.test(phone)) {
      Taro.showToast({ title: '请输入 11 位手机号', icon: 'none' });
      return false;
    }
    if (password.length < 6) {
      Taro.showToast({ title: '密码至少 6 位', icon: 'none' });
      return false;
    }
    return true;
  };

  // 登录
  const handleLogin = async () => {
    if (submitting || !validate()) return;
    setSubmitting(true);
    try {
      const res = await AuthAPI.login(phone, password);
      Taro.showToast({ title: `欢迎回来, ${res.nickname}`, icon: 'success' });
      setTimeout(() => Taro.navigateBack(), 1200);
    } catch (e) {
      console.warn('[login] 登录失败:', e);
    } finally {
      setSubmitting(false);
    }
  };

  // 注册(成功后自动登录)
  const handleRegister = async () => {
    if (submitting || !validate()) return;
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

  // 测试账号快捷填充
  const fillAccount = (testPhone: string, testPwd: string) => {
    setPhone(testPhone);
    setPassword(testPwd);
    Taro.showToast({ title: '已填充, 点击登录', icon: 'none' });
  };

  return (
    <View className={styles.page}>
      {/* 品牌头 */}
      <View className={styles.brand}>
        <View className={styles.brandIcon}>🍶</View>
        <View className={styles.brandTitle}>竹香酒</View>
        <View className={styles.brandDesc}>竹韵佳酿 · 雅致生活</View>
      </View>

      {/* 表单卡片 */}
      <View className={styles.card}>
        {/* 模式切换 */}
        <View className={styles.tabs}>
          <View
            className={`${styles.tab} ${mode === 'login' ? styles.tabActive : ''}`}
            onClick={() => setMode('login')}
          >
            登录
          </View>
          <View
            className={`${styles.tab} ${mode === 'register' ? styles.tabActive : ''}`}
            onClick={() => setMode('register')}
          >
            注册
          </View>
        </View>

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

        {mode === 'register' ? (
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
        ) : null}

        <View className={styles.formItem}>
          <Text className={styles.label}>密码</Text>
          <View className={styles.inputWrap}>
            <Input
              className={styles.input}
              type='text'
              password
              maxlength={64}
              value={password}
              placeholder={mode === 'register' ? '设置密码(至少 6 位)' : '请输入密码'}
              onInput={(e) => setPassword(e.detail.value)}
            />
          </View>
        </View>

        <View
          className={styles.submitBtn}
          onClick={mode === 'login' ? handleLogin : handleRegister}
        >
          {submitting
            ? (mode === 'login' ? '登录中...' : '注册中...')
            : (mode === 'login' ? '登 录' : '注册并登录')}
        </View>

        <View className={styles.tip}>
          {mode === 'login'
            ? '还没有账号?点击上方「注册」创建'
            : '注册即代表同意《用户协议》与《隐私政策》'}
        </View>

        {mode === 'login' && (
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
    </View>
  );
};

export default LoginPage;
