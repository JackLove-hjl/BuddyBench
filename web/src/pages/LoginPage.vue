<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NForm, NFormItem, NInput, useMessage } from 'naive-ui'
import { useAuthStore } from '../stores/auth'
import BrandLogo from '../components/common/BrandLogo.vue'

const router = useRouter()
const authStore = useAuthStore()
const message = useMessage()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const loading = ref(false)

function switchMode(m: 'login' | 'register') {
  mode.value = m
  confirmPassword.value = ''
}

async function onSubmit() {
  if (!username.value.trim() || !password.value) {
    message.warning('请输入用户名和密码')
    return
  }
  if (mode.value === 'register') {
    if (password.value.length < 6) {
      message.warning('密码至少 6 位')
      return
    }
    if (password.value !== confirmPassword.value) {
      message.warning('两次输入的密码不一致')
      return
    }
  }
  loading.value = true
  try {
    if (mode.value === 'login') {
      await authStore.login(username.value.trim(), password.value)
    } else {
      await authStore.register(username.value.trim(), password.value)
    }
    message.success(mode.value === 'login' ? '登录成功' : '注册成功')
    void router.push('/')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <span class="login-mark">
          <BrandLogo :size="44" />
        </span>
        <h1 class="login-title">BuddyBench</h1>
        <p class="login-sub">智能体工作台</p>
      </div>

      <div class="login-tabs">
        <button class="login-tab" :class="{ active: mode === 'login' }" @click="switchMode('login')">登录</button>
        <button class="login-tab" :class="{ active: mode === 'register' }" @click="switchMode('register')">注册</button>
      </div>

      <n-form class="login-form" label-placement="top" size="large">
        <n-form-item label="用户名">
          <n-input v-model:value="username" placeholder="请输入用户名" autocomplete="username" @keydown.enter="onSubmit" />
        </n-form-item>
        <n-form-item label="密码">
          <n-input v-model:value="password" type="password" show-password-on="click" placeholder="请输入密码" autocomplete="current-password" @keydown.enter="onSubmit" />
        </n-form-item>
        <n-form-item v-if="mode === 'register'" label="确认密码">
          <n-input v-model:value="confirmPassword" type="password" show-password-on="click" placeholder="再次输入密码" autocomplete="new-password" @keydown.enter="onSubmit" />
        </n-form-item>
      </n-form>

      <n-button class="login-btn" type="primary" size="large" block :loading="loading" @click="onSubmit">
        {{ mode === 'login' ? '登 录' : '注 册' }}
      </n-button>
    </div>
    <div class="login-footer">内容由 AI 生成,请注意甄别</div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--bg);
  gap: 24px;
}
.login-card {
  width: 400px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 36px 36px 28px;
  box-shadow: var(--shadow);
}
.login-logo {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  margin-bottom: 24px;
}
.login-mark {
  color: var(--accent);
  display: flex;
  align-items: center;
}
.login-title {
  margin: 8px 0 0;
  font-size: 26px;
  font-weight: 700;
  color: var(--text);
}
.login-sub {
  margin: 0;
  font-size: 13px;
  color: var(--text-tertiary);
}
.login-tabs {
  display: flex;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.login-tab {
  flex: 1;
  border: none;
  background: transparent;
  padding: 10px 0;
  font-size: 15px;
  color: var(--text-secondary);
  cursor: pointer;
  font-family: inherit;
  border-bottom: 2px solid transparent;
  transition: color 0.15s, border-color 0.15s;
}
.login-tab:hover {
  color: var(--text);
}
.login-tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
.login-form {
  margin-bottom: 8px;
}
.login-btn {
  margin-top: 8px;
  font-weight: 600;
}
.login-footer {
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
