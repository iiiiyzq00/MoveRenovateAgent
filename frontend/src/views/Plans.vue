<template>
  <div class="plans-page">
    <div class="plans-header">
      <h2>📋 历史方案</h2>
      <p class="plans-sub">跨会话恢复你的搬家+装修方案，继续上次的规划</p>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="loading-spinner"></div>
      <p>加载中...</p>
    </div>

    <div v-else-if="plans.length === 0" class="empty-state glass-card">
      <div class="empty-icon">📭</div>
      <h3>暂无历史方案</h3>
      <p>开始一段新的对话，生成的方案会自动保存到这里</p>
      <router-link to="/" class="btn-primary" style="display:inline-block;text-decoration:none;margin-top:16px;">
        开始对话 →
      </router-link>
    </div>

    <div v-else class="plan-grid">
      <div
        v-for="plan in plans"
        :key="plan.plan_id"
        class="plan-card glass-card"
        @click="openPlan(plan.plan_id)"
      >
        <div class="plan-top">
          <span class="plan-version">v{{ plan.plan_version }}</span>
          <span class="plan-date">{{ formatDate(plan.created_at) }}</span>
        </div>
        <p class="plan-summary">{{ plan.summary }}</p>
        <div class="plan-meta">
          <span v-if="plan.key_info.from" class="meta-item">📍 {{ plan.key_info.from }} → {{ plan.key_info.to }}</span>
          <span v-if="plan.key_info.volume_m3" class="meta-item">📦 {{ plan.key_info.volume_m3 }}m³</span>
          <span v-if="plan.key_info.budget_yuan" class="meta-item">💰 ¥{{ plan.key_info.budget_yuan?.toLocaleString() }}</span>
          <span v-if="plan.key_info.style" class="meta-item">🎨 {{ plan.key_info.style }}</span>
        </div>
        <div class="plan-arrow">→</div>
      </div>
    </div>

    <router-link to="/" class="back-link">← 返回聊天</router-link>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const API_BASE = '/api'
const plans = ref<any[]>([])
const loading = ref(true)

onMounted(async () => {
  const uid = localStorage.getItem('user_id') || 'anonymous'
  try {
    const resp = await fetch(`${API_BASE}/plans?user_id=${uid}`)
    const data = await resp.json()
    plans.value = data.plans || []
  } catch (e) {
    console.error('Failed to load plans:', e)
  } finally {
    loading.value = false
  }
})

function openPlan(id: string) {
  router.push(`/plans/${id}`)
}

function formatDate(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  const mins = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}
</script>

<style scoped>
.plans-page {
  padding: 8px 4px;
  overflow-y: auto;
}

.plans-header {
  margin-bottom: 20px;
}
.plans-header h2 {
  font-size: 1.4em;
  font-weight: 700;
  margin-bottom: 4px;
}
.plans-sub {
  color: var(--text-secondary);
  font-size: 0.9em;
}

/* ═══════════════════════════════════════════════════════════ */
/* 加载 & 空状态                                               */
/* ═══════════════════════════════════════════════════════════ */
.loading-state {
  text-align: center;
  padding: 60px;
  color: var(--text-muted);
}
.loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid rgba(99, 102, 241, 0.15);
  border-top-color: var(--accent-1);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 12px;
}
@keyframes spin { to { transform: rotate(360deg); } }

.empty-state {
  text-align: center;
  padding: 60px 40px;
  max-width: 460px;
  margin: 40px auto;
}
.empty-icon { font-size: 3em; margin-bottom: 12px; }
.empty-state h3 { font-size: 1.2em; margin-bottom: 6px; }
.empty-state p { color: var(--text-secondary); font-size: 0.9em; }

/* ═══════════════════════════════════════════════════════════ */
/* 方案卡片网格                                                */
/* ═══════════════════════════════════════════════════════════ */
.plan-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 14px;
}

.plan-card {
  padding: 20px 24px;
  cursor: pointer;
  transition: all 0.25s;
  position: relative;
  overflow: hidden;
}
.plan-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 12px 36px rgba(31, 38, 135, 0.12);
  border-color: rgba(99, 102, 241, 0.25);
}
.plan-card::after {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 4px; height: 100%;
  background: linear-gradient(180deg, var(--accent-1), var(--accent-2));
  opacity: 0;
  transition: opacity 0.25s;
  border-radius: 4px 0 0 4px;
}
.plan-card:hover::after { opacity: 1; }

.plan-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.plan-version {
  padding: 3px 12px;
  border-radius: 20px;
  font-size: 0.8em;
  font-weight: 600;
  background: linear-gradient(135deg, rgba(99,102,241,0.1), rgba(168,85,247,0.1));
  color: var(--accent-1);
}
.plan-date {
  font-size: 0.82em;
  color: var(--text-muted);
}

.plan-summary {
  font-size: 0.94em;
  line-height: 1.6;
  color: var(--text-primary);
  margin-bottom: 12px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.plan-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.meta-item {
  font-size: 0.82em;
  color: var(--text-secondary);
  padding: 4px 10px;
  background: rgba(0,0,0,.03);
  border-radius: 6px;
}

.plan-arrow {
  position: absolute;
  right: 20px;
  bottom: 20px;
  font-size: 1.2em;
  color: var(--text-muted);
  opacity: 0;
  transition: all 0.25s;
}
.plan-card:hover .plan-arrow {
  opacity: 1;
  transform: translateX(4px);
}

.back-link {
  display: inline-block;
  margin-top: 24px;
  color: var(--accent-1);
  text-decoration: none;
  font-weight: 500;
  font-size: 0.9em;
}
.back-link:hover { text-decoration: underline; }
</style>
