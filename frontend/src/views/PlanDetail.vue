<template>
  <div class="detail-page">
    <div v-if="loading" class="loading-state">
      <div class="loading-spinner"></div>
      <p>加载中...</p>
    </div>

    <div v-else-if="!plan" class="empty-state glass-card">
      <div class="empty-icon">🔍</div>
      <h3>方案不存在</h3>
      <p>该方案可能已被删除或链接无效</p>
      <router-link to="/plans" class="btn-back">← 返回列表</router-link>
    </div>

    <div v-else class="plan-content">
      <!-- 头部 -->
      <div class="detail-header glass-card">
        <div class="header-row">
          <div>
            <h2>📋 方案 v{{ plan.plan_version }}</h2>
            <p class="header-summary">{{ plan.summary }}</p>
          </div>
          <span class="header-date">{{ formatFullDate(plan.created_at) }}</span>
        </div>
      </div>

      <div class="detail-columns">
        <!-- 搬家方案 -->
        <section v-if="plan.snapshot?.moving_state?.from_address" class="section-card glass-card">
          <h3>🚛 搬家方案</h3>
          <table class="info-table">
            <tr><td class="t-label">搬出</td><td>{{ plan.snapshot.moving_state.from_address }}</td></tr>
            <tr><td class="t-label">搬入</td><td>{{ plan.snapshot.moving_state.to_address }}</td></tr>
            <tr v-if="plan.snapshot.moving_state.total_volume_m3">
              <td class="t-label">总体积</td>
              <td><span class="highlight">{{ plan.snapshot.moving_state.total_volume_m3 }}m³</span></td>
            </tr>
            <tr v-if="plan.snapshot.moving_state.vehicle_recommendation">
              <td class="t-label">推荐车型</td>
              <td>
                <span class="badge">{{ plan.snapshot.moving_state.vehicle_recommendation.type }}</span>
              </td>
            </tr>
            <tr v-if="plan.snapshot.moving_state.freight_estimate?.price_range">
              <td class="t-label">运费</td>
              <td class="price">
                ¥{{ plan.snapshot.moving_state.freight_estimate.price_range.min_yuan }}
                <span class="price-sep">–</span>
                ¥{{ plan.snapshot.moving_state.freight_estimate.price_range.max_yuan }}
              </td>
            </tr>
            <tr v-if="plan.snapshot.moving_state.route?.distance_km">
              <td class="t-label">距离</td><td>{{ plan.snapshot.moving_state.route.distance_km }}km</td>
            </tr>
          </table>

          <div v-if="plan.snapshot.moving_state.inventory?.length" class="subsection">
            <h4>物品清单 ({{ plan.snapshot.moving_state.inventory.length }}件)</h4>
            <div class="item-chips">
              <span v-for="item in plan.snapshot.moving_state.inventory.slice(0, 15)" :key="item.item_id" class="chip">
                {{ item.name }} ×{{ item.quantity }}
                <small>{{ item.estimated_volume_m3 }}m³</small>
              </span>
              <span v-if="plan.snapshot.moving_state.inventory.length > 15" class="chip chip-more">
                +{{ plan.snapshot.moving_state.inventory.length - 15 }} 件
              </span>
            </div>
          </div>
        </section>

        <!-- 装修方案 -->
        <section v-if="plan.snapshot?.renovation_state?.total_budget_yuan" class="section-card glass-card">
          <h3>🏗️ 装修方案</h3>
          <table class="info-table">
            <tr>
              <td class="t-label">预算</td>
              <td class="price">¥{{ plan.snapshot.renovation_state.total_budget_yuan?.toLocaleString() }}</td>
            </tr>
            <tr v-if="plan.snapshot.renovation_state.house_area_m2">
              <td class="t-label">面积</td><td>{{ plan.snapshot.renovation_state.house_area_m2 }}m²</td>
            </tr>
            <tr v-if="plan.snapshot.renovation_state.style_preference">
              <td class="t-label">风格</td>
              <td><span class="badge">{{ plan.snapshot.renovation_state.style_preference }}</span></td>
            </tr>
            <tr v-if="plan.snapshot.renovation_state.construction_phases?.length">
              <td class="t-label">施工阶段</td>
              <td>{{ plan.snapshot.renovation_state.construction_phases.length }} 个</td>
            </tr>
          </table>

          <div v-if="plan.snapshot.renovation_state.budget_allocation" class="subsection">
            <h4>预算分配</h4>
            <div class="budget-bars">
              <div
                v-for="(val, key) in plan.snapshot.renovation_state.budget_allocation"
                :key="key"
                class="budget-bar"
              >
                <div class="bar-label">
                  <span>{{ labelMap[key] || key }}</span>
                  <span>{{ (val.percentage * 100).toFixed(0) }}%</span>
                </div>
                <div class="bar-track">
                  <div
                    class="bar-fill"
                    :style="{
                      width: (val.percentage * 100) + '%',
                      background: barColors[key] || 'linear-gradient(135deg, #6366f1, #a855f7)'
                    }"
                  ></div>
                </div>
                <div class="bar-amount">¥{{ val.amount?.toLocaleString() }}</div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- 操作 -->
      <div class="detail-actions">
        <button @click="restorePlan" class="btn-restore">
          🔄 恢复此方案到新会话
        </button>
        <router-link to="/plans" class="back-link">← 返回历史列表</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const API_BASE = '/api'
const plan = ref<any>(null)
const loading = ref(true)

const labelMap: Record<string, string> = {
  hard_fixture: '硬装',
  soft_furnishing: '软装',
  appliances: '家电',
  reserve: '备用金',
}

const barColors: Record<string, string> = {
  hard_fixture: 'linear-gradient(135deg, #6366f1, #818cf8)',
  soft_furnishing: 'linear-gradient(135deg, #a855f7, #c084fc)',
  appliances: 'linear-gradient(135deg, #ec4899, #f472b6)',
  reserve: 'linear-gradient(135deg, #6b7280, #9ca3af)',
}

onMounted(async () => {
  try {
    const resp = await fetch(`${API_BASE}/plans/${route.params.id}`)
    const data = await resp.json()
    plan.value = data.plan
  } catch (e) {
    console.error('Failed to load plan:', e)
  } finally {
    loading.value = false
  }
})

async function restorePlan() {
  try {
    const resp = await fetch(`${API_BASE}/plans/${route.params.id}/restore`, { method: 'POST' })
    const data = await resp.json()
    if (data.success) {
      localStorage.setItem('session_id', '')
      router.push('/')
    }
  } catch (e) {
    alert('恢复失败: ' + e)
  }
}

function formatFullDate(iso: string): string {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}
</script>

<style scoped>
.detail-page { overflow-y: auto; padding: 4px; }

/* ═══════════════════════════════════════════════════════════ */
/* 加载 / 空状态                                               */
/* ═══════════════════════════════════════════════════════════ */
.loading-state { text-align: center; padding: 60px; color: var(--text-muted); }
.loading-spinner {
  width: 36px; height: 36px;
  border: 3px solid rgba(99,102,241,.15);
  border-top-color: var(--accent-1);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto 12px;
}
@keyframes spin { to { transform: rotate(360deg); } }

.empty-state { text-align: center; padding: 60px 40px; max-width: 400px; margin: 40px auto; }
.empty-icon { font-size: 3em; margin-bottom: 12px; }
.empty-state h3 { margin-bottom: 6px; }
.empty-state p { color: var(--text-secondary); margin-bottom: 16px; }
.btn-back { color: var(--accent-1); text-decoration: none; font-weight: 500; }

/* ═══════════════════════════════════════════════════════════ */
/* 头部                                                        */
/* ═══════════════════════════════════════════════════════════ */
.detail-header {
  padding: 20px 28px;
  margin-bottom: 18px;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}
.header-row h2 { font-size: 1.3em; margin-bottom: 6px; }
.header-summary { color: var(--text-secondary); font-size: 0.92em; }
.header-date { color: var(--text-muted); font-size: 0.85em; white-space: nowrap; }

/* ═══════════════════════════════════════════════════════════ */
/* 双栏布局                                                    */
/* ═══════════════════════════════════════════════════════════ */
.detail-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

@media (max-width: 700px) {
  .detail-columns { grid-template-columns: 1fr; }
}

/* ═══════════════════════════════════════════════════════════ */
/* 方案卡片                                                    */
/* ═══════════════════════════════════════════════════════════ */
.section-card {
  padding: 22px 26px;
}
.section-card h3 {
  font-size: 1.1em;
  margin-bottom: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(0,0,0,.05);
}

/* ═══════════════════════════════════════════════════════════ */
/* 信息表格                                                    */
/* ═══════════════════════════════════════════════════════════ */
.info-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92em;
}
.info-table td {
  padding: 8px 0;
  border-bottom: 1px solid rgba(0,0,0,.04);
}
.t-label { color: var(--text-muted); width: 80px; font-size: 0.9em; }
.highlight { font-weight: 700; color: var(--accent-1); }
.price { font-weight: 600; color: var(--text-primary); }
.price-sep { color: var(--text-muted); margin: 0 4px; }

.badge {
  padding: 3px 12px;
  border-radius: 20px;
  font-size: 0.88em;
  background: linear-gradient(135deg, rgba(99,102,241,0.08), rgba(168,85,247,0.08));
  color: var(--accent-1);
  font-weight: 500;
}

/* ═══════════════════════════════════════════════════════════ */
/* 物品标签                                                    */
/* ═══════════════════════════════════════════════════════════ */
.subsection { margin-top: 18px; }
.subsection h4 { font-size: 0.95em; margin-bottom: 10px; color: var(--text-secondary); }
.item-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  font-size: 0.82em;
  padding: 5px 12px;
  background: rgba(0,0,0,.03);
  border-radius: 20px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.chip small { color: var(--text-muted); }
.chip-more { background: rgba(99, 102, 241, 0.08); color: var(--accent-1); }

/* ═══════════════════════════════════════════════════════════ */
/* 预算进度条                                                  */
/* ═══════════════════════════════════════════════════════════ */
.budget-bars { display: flex; flex-direction: column; gap: 14px; }
.budget-bar { }
.bar-label {
  display: flex; justify-content: space-between;
  font-size: 0.88em; margin-bottom: 4px;
  color: var(--text-secondary);
}
.bar-track {
  height: 8px;
  background: rgba(0,0,0,.05);
  border-radius: 4px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.6s ease;
}
.bar-amount {
  font-size: 0.82em;
  color: var(--text-muted);
  margin-top: 3px;
  font-weight: 500;
}

/* ═══════════════════════════════════════════════════════════ */
/* 操作按钮                                                    */
/* ═══════════════════════════════════════════════════════════ */
.detail-actions {
  display: flex;
  gap: 20px;
  align-items: center;
  margin-top: 24px;
  padding-bottom: 24px;
}
.btn-restore {
  padding: 12px 28px;
  background: linear-gradient(135deg, var(--accent-1), var(--accent-2));
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 1em;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.25s;
  box-shadow: 0 4px 15px rgba(99, 102, 241, 0.3);
}
.btn-restore:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(99, 102, 241, 0.4);
}
.back-link { color: var(--accent-1); text-decoration: none; font-weight: 500; font-size: 0.9em; }
</style>
