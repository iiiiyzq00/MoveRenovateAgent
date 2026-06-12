<template>
  <div class="chat-page">
    <!-- 消息区域 -->
    <div class="messages" ref="messagesEl">
      <div v-if="messages.length === 0 && !loading" class="empty-state glass-card">
        <div class="empty-icon">🏠</div>
        <h2>你好，我是搬装规划助手</h2>
        <p>基于 LLM 的智能搬家+装修一体化方案，支持多轮增量调整</p>
        <div class="quick-examples">
          <button v-for="ex in quickExamples" :key="ex" class="quick-btn" @click="quickSend(ex)">
            {{ ex }}
          </button>
        </div>
      </div>

      <div v-for="(msg, i) in messages" :key="i" class="message-round">
        <!-- 用户消息 -->
        <div class="msg-user-row">
          <div class="msg-avatar user-avatar">👤</div>
          <div class="msg-bubble user-bubble">{{ msg.user }}</div>
        </div>

        <!-- AI 消息 -->
        <div class="msg-ai-row">
          <div class="msg-avatar ai-avatar">🤖</div>
          <div class="msg-body">
            <div class="msg-bubble ai-bubble markdown-body" v-html="renderMarkdown(msg.assistant)"></div>

            <!-- Skill 标签 -->
            <div v-if="msg.skills && msg.skills.length" class="msg-tags">
              <span v-for="s in msg.skills" :key="s" class="tag-item">{{ skillLabel(s) }}</span>
            </div>

            <!-- 决策依据 -->
            <div v-if="msg.traces && msg.traces.length" class="msg-traces glass-card">
              <details>
                <summary>📋 决策依据 ({{ msg.traces.length }}条)</summary>
                <ul><li v-for="(t, j) in msg.traces" :key="j">{{ t }}</li></ul>
              </details>
            </div>

            <!-- 工具调用 -->
            <div v-if="msg.tools && msg.tools.length" class="msg-traces glass-card">
              <details>
                <summary>🔧 工具调用记录 ({{ msg.tools.length }}次)</summary>
                <ul><li v-for="(t, j) in msg.tools" :key="j">{{ t.tool }}: {{ t.args_summary || '' }}</li></ul>
              </details>
            </div>
          </div>
        </div>
      </div>

      <!-- 加载动画 -->
      <div v-if="loading" class="loading-row">
        <div class="msg-avatar ai-avatar">🤖</div>
        <div class="typing-indicator glass-card">
          <span></span><span></span><span></span>
        </div>
        <span v-if="streamingText" class="streaming-hint">{{ streamingText }}</span>
      </div>
    </div>

    <!-- 输入区域（毛玻璃） -->
    <div class="input-area glass-card">
      <input
        v-model="input"
        type="text"
        placeholder="描述你的搬装需求，例如：三居室90平，预算15万，有猫，朝阳搬到海淀..."
        :disabled="loading"
        class="input-glass chat-input"
        @keydown.enter="send"
      />
      <button
        @click="send"
        :disabled="loading || !input.trim()"
        class="send-btn"
      >
        <span v-if="!loading">发送</span>
        <span v-else class="sending-dot">●</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { marked } from 'marked'

const API_BASE = '/api'

interface Message {
  user: string
  assistant: string
  traces?: string[]
  skills?: string[]
  tools?: { tool: string; args_summary?: string }[]
}

const messages = ref<Message[]>([])
const input = ref('')
const loading = ref(false)
const streamingText = ref('')
const sessionId = ref(localStorage.getItem('session_id') || '')
const userId = ref(localStorage.getItem('user_id') || '')
const messagesEl = ref<HTMLElement>()

const quickExamples = [
  '三居室90平，预算15万，有猫，北欧风，朝阳→海淀',
  '两居室60平，预算10万，简约风，浦东→徐汇',
  '家里有老人需要适老化改造',
]

const skillLabelMap: Record<string, string> = {
  pet_relocation: '🐱 宠物搬运',
  heavy_lifting: '🏗️ 大件吊装',
  elderly_accessible: '👴 适老化改造',
  plant_moving: '🌿 绿植搬运',
}

function skillLabel(id: string): string {
  return skillLabelMap[id] || id
}

function quickSend(text: string) {
  input.value = text
  send()
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  input.value = ''
  loading.value = true
  streamingText.value = ''

  try {
    const resp = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId.value || null,
        user_id: userId.value || null,
        message: text,
        stream: true,
      }),
    })

    const reader = resp.body?.getReader()
    if (!reader) { await sendRest(text); return }

    const decoder = new TextDecoder()
    let buffer = ''
    let finalTraces: string[] = []
    let finalSkills: string[] = []
    let finalTools: { tool: string; args_summary?: string }[] = []

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const data = JSON.parse(line.slice(6))
          switch (data.type) {
            case 'start':
              sessionId.value = data.session_id
              localStorage.setItem('session_id', data.session_id)
              break
            case 'intent':
              streamingText.value = `🎯 ${data.intent}`
              break
            case 'complete':
              finalTraces = data.recent_traces || []
              finalSkills = data.active_skills || []
              finalTools = (data.tool_call_log || []).map((t: any) => ({
                tool: t.tool,
                args_summary: t.args ? Object.entries(t.args).map(([k,v]: any) => `${k}=${v}`).join(', ') : '',
              }))
              messages.value.push({
                user: text,
                assistant: data.response || '',
                traces: finalTraces,
                skills: finalSkills,
                tools: finalTools,
              })
              await nextTick()
              messagesEl.value?.scrollTo({ top: messagesEl.value.scrollHeight, behavior: 'smooth' })
              break
          }
        } catch { /* partial SSE */ }
      }
    }
  } catch (e) {
    messages.value.push({ user: text, assistant: `⚠️ 请求失败: ${e}` })
  } finally {
    loading.value = false
    streamingText.value = ''
    await nextTick()
    messagesEl.value?.scrollTo({ top: messagesEl.value.scrollHeight, behavior: 'smooth' })
  }
}

async function sendRest(text: string) {
  try {
    const resp = await fetch(`${API_BASE}/chat`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId.value || null,
        user_id: userId.value || null,
        message: text, stream: false,
      }),
    })
    const data = await resp.json()
    sessionId.value = data.session_id
    localStorage.setItem('session_id', data.session_id)
    messages.value.push({
      user: text, assistant: data.response || '',
      traces: data.recent_traces || [],
      skills: data.active_skills || [],
      tools: (data.tool_call_log || []).map((t: any) => ({
        tool: t.tool,
        args_summary: t.args ? Object.entries(t.args).map(([k,v]: any) => `${k}=${v}`).join(', ') : '',
      })),
    })
  } catch (e) {
    messages.value.push({ user: text, assistant: `⚠️ 请求失败: ${e}` })
  } finally {
    loading.value = false
    await nextTick()
    messagesEl.value?.scrollTo({ top: messagesEl.value.scrollHeight, behavior: 'smooth' })
  }
}

function renderMarkdown(md: string): string {
  return marked.parse(md, { breaks: true }) as string
}
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 12px;
}

/* ═══════════════════════════════════════════════════════════ */
/* 消息区域                                                    */
/* ═══════════════════════════════════════════════════════════ */
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 8px 4px;
}

/* ═══════════════════════════════════════════════════════════ */
/* 空状态                                                      */
/* ═══════════════════════════════════════════════════════════ */
.empty-state {
  text-align: center;
  padding: 60px 40px;
  max-width: 520px;
  margin: 60px auto;
}
.empty-icon { font-size: 3em; margin-bottom: 12px; }
.empty-state h2 { font-size: 1.4em; margin-bottom: 8px; font-weight: 700; }
.empty-state p { color: var(--text-secondary); margin-bottom: 24px; }

.quick-examples {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
}
.quick-btn {
  padding: 10px 20px;
  background: var(--glass-bg-light);
  backdrop-filter: blur(10px);
  border: 1px solid var(--glass-border);
  border-radius: 20px;
  cursor: pointer;
  font-size: 0.9em;
  color: var(--text-secondary);
  transition: all 0.2s;
  max-width: 400px;
  width: 100%;
}
.quick-btn:hover {
  background: rgba(99, 102, 241, 0.08);
  color: var(--accent-1);
  border-color: rgba(99, 102, 241, 0.25);
  transform: translateX(4px);
}

/* ═══════════════════════════════════════════════════════════ */
/* 消息气泡                                                    */
/* ═══════════════════════════════════════════════════════════ */
.message-round { margin-bottom: 24px; }

.msg-user-row {
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 8px;
}

.msg-ai-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.msg-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1em;
  flex-shrink: 0;
}
.user-avatar {
  background: linear-gradient(135deg, var(--accent-1), var(--accent-2));
  color: white;
}
.ai-avatar {
  background: linear-gradient(135deg, #f0f0ff, #e8e8ff);
  border: 1px solid rgba(99, 102, 241, 0.15);
}

.msg-bubble {
  padding: 12px 18px;
  border-radius: var(--radius-md);
  line-height: 1.65;
  font-size: 0.94em;
  max-width: 85%;
}

.user-bubble {
  background: linear-gradient(135deg, var(--accent-1), var(--accent-2));
  color: #fff;
  border-bottom-right-radius: 4px;
}

.ai-bubble {
  background: var(--glass-bg-strong);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-bottom-left-radius: 4px;
  box-shadow: 0 2px 12px rgba(0,0,0,.04);
}

.msg-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

/* ═══════════════════════════════════════════════════════════ */
/* 标签 & 溯源                                                 */
/* ═══════════════════════════════════════════════════════════ */
.msg-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding-left: 4px;
}
.tag-item {
  font-size: 0.8em;
  padding: 3px 12px;
  border-radius: 20px;
  background: linear-gradient(135deg, rgba(99,102,241,0.08), rgba(168,85,247,0.08));
  border: 1px solid rgba(99, 102, 241, 0.12);
  color: var(--accent-1);
  font-weight: 500;
}

.msg-traces {
  font-size: 0.84em;
  padding: 10px 16px;
  margin-left: 4px;
  border-radius: var(--radius-sm) !important;
}
.msg-traces summary {
  cursor: pointer;
  color: var(--text-secondary);
  font-weight: 500;
}
.msg-traces ul {
  margin: 6px 0 0 18px;
  color: var(--text-muted);
  line-height: 1.7;
}

/* ═══════════════════════════════════════════════════════════ */
/* 加载动画                                                    */
/* ═══════════════════════════════════════════════════════════ */
.loading-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px;
}

.typing-indicator {
  display: flex;
  gap: 5px;
  padding: 14px 20px;
  border-radius: var(--radius-md) !important;
  border-bottom-left-radius: 4px !important;
}
.typing-indicator span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent-1);
  animation: typing 1.4s infinite;
  opacity: 0.4;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 60%, 100% { opacity: 0.4; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-4px); }
}

.streaming-hint {
  font-size: 0.85em;
  color: var(--text-muted);
}

/* ═══════════════════════════════════════════════════════════ */
/* 输入区域（毛玻璃）                                          */
/* ═══════════════════════════════════════════════════════════ */
.input-area {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: var(--radius-lg);
  flex-shrink: 0;
}

.chat-input {
  flex: 1;
  border: none !important;
  background: transparent !important;
  backdrop-filter: none !important;
  padding: 14px 8px !important;
  box-shadow: none !important;
}
.chat-input:focus {
  box-shadow: none !important;
  border: none !important;
}

.send-btn {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent-1), var(--accent-2));
  color: #fff;
  border: none;
  font-size: 0.9em;
  font-weight: 600;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.25s;
  box-shadow: 0 4px 15px rgba(99, 102, 241, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
}
.send-btn:hover:not(:disabled) {
  transform: scale(1.05);
  box-shadow: 0 6px 20px rgba(99, 102, 241, 0.45);
}
.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  box-shadow: none;
}

.sending-dot {
  animation: pulse 0.8s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
</style>
