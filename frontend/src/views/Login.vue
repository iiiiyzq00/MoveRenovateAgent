<template>
  <div class="login-page">
    <div class="login-card glass-card">
      <div class="login-icon">🏠</div>
      <h2>智能搬装规划 Agent</h2>
      <p class="sub">基于 LLM 的搬家+装修一体化规划助手</p>

      <form @submit.prevent="login" class="login-form">
        <label>用户标识</label>
        <div class="input-wrapper">
          <span class="input-prefix">@</span>
          <input
            v-model="userId"
            type="text"
            placeholder="输入已有 ID，或留空自动创建"
            class="input-glass"
          />
        </div>
        <p class="hint">
          <span v-if="hasExistingId">✅ 检测到上次使用的 ID，点击进入即可恢复历史方案</span>
          <span v-else>首次使用将自动创建新账户，后续可跨会话恢复方案和偏好</span>
        </p>
        <button type="submit" class="btn-login">
          进入系统
          <span class="btn-arrow">→</span>
        </button>
      </form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const userId = ref(localStorage.getItem('user_id') || '')

const hasExistingId = computed(() => !!localStorage.getItem('user_id'))

function login() {
  const id = userId.value.trim() || 'user_' + Date.now().toString(36)
  localStorage.setItem('user_id', id)
  localStorage.setItem('session_id', '')
  router.push('/')
}
</script>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  padding: 24px;
}

.login-card {
  max-width: 440px;
  width: 100%;
  padding: 48px 40px;
  text-align: center;
}

.login-icon {
  font-size: 3em;
  margin-bottom: 12px;
}

.login-card h2 {
  font-size: 1.5em;
  font-weight: 700;
  margin-bottom: 6px;
  background: linear-gradient(135deg, var(--accent-1), var(--accent-2));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.sub {
  color: var(--text-secondary);
  font-size: 0.92em;
  margin-bottom: 32px;
}

.login-form {
  text-align: left;
}

label {
  display: block;
  font-weight: 600;
  margin-bottom: 8px;
  font-size: 0.9em;
  color: var(--text-secondary);
}

.input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}
.input-prefix {
  position: absolute;
  left: 16px;
  color: var(--text-muted);
  font-weight: 500;
  z-index: 1;
}
.input-wrapper .input-glass {
  padding-left: 36px;
}

.hint {
  margin-top: 10px;
  color: var(--text-muted);
  font-size: 0.82em;
  line-height: 1.5;
}

.btn-login {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  margin-top: 24px;
  padding: 14px;
  background: linear-gradient(135deg, var(--accent-1), var(--accent-2));
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 1.1em;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.25s;
  box-shadow: 0 4px 20px rgba(99, 102, 241, 0.3);
}
.btn-login:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 30px rgba(99, 102, 241, 0.4);
}
.btn-arrow {
  transition: transform 0.2s;
}
.btn-login:hover .btn-arrow {
  transform: translateX(4px);
}
</style>
