<template>
  <div class="app">
    <!-- 背景装饰 -->
    <div class="bg-blob bg-blob-1"></div>
    <div class="bg-blob bg-blob-2"></div>
    <div class="bg-blob bg-blob-3"></div>

    <header class="header">
      <div class="header-left">
        <router-link to="/" class="logo">
          <span class="logo-icon">🏠</span>
          <span class="logo-text">智能搬装规划</span>
        </router-link>
        <span class="subtitle">Agent · 搬家+装修一体化</span>
      </div>
      <nav class="header-nav">
        <router-link to="/" class="nav-link"><span class="nav-icon">💬</span>对话</router-link>
        <router-link to="/plans" class="nav-link"><span class="nav-icon">📋</span>历史方案</router-link>
        <router-link to="/login" class="nav-link"><span class="nav-icon">👤</span>账户</router-link>
      </nav>
    </header>
    <main class="main-content">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
  </div>
</template>

<style>
/* ═══════════════════════════════════════════════════════════ */
/* 全局基础                                                    */
/* ═══════════════════════════════════════════════════════════ */
:root {
  --glass-bg: rgba(255, 255, 255, 0.72);
  --glass-bg-strong: rgba(255, 255, 255, 0.88);
  --glass-bg-light: rgba(255, 255, 255, 0.55);
  --glass-border: rgba(255, 255, 255, 0.6);
  --glass-shadow: 0 8px 32px rgba(31, 38, 135, 0.10);
  --glass-blur: blur(20px) saturate(180%);
  --accent-1: #6366f1;
  --accent-2: #a855f7;
  --accent-3: #ec4899;
  --text-primary: #1e1b4b;
  --text-secondary: #6b7280;
  --text-muted: #9ca3af;
  --radius-lg: 20px;
  --radius-md: 14px;
  --radius-sm: 10px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', sans-serif;
  background: linear-gradient(135deg, #eef2ff 0%, #fae8ff 30%, #fce7f3 60%, #ecfdf5 100%);
  min-height: 100vh;
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
}

/* ═══════════════════════════════════════════════════════════ */
/* 背景装饰气泡                                                */
/* ═══════════════════════════════════════════════════════════ */
.bg-blob {
  position: fixed;
  border-radius: 50%;
  filter: blur(80px);
  opacity: 0.45;
  pointer-events: none;
  z-index: 0;
}
.bg-blob-1 {
  width: 500px; height: 500px;
  background: radial-gradient(circle, rgba(99,102,241,0.25), transparent);
  top: -150px; right: -100px;
}
.bg-blob-2 {
  width: 400px; height: 400px;
  background: radial-gradient(circle, rgba(168,85,247,0.20), transparent);
  bottom: -100px; left: -80px;
}
.bg-blob-3 {
  width: 350px; height: 350px;
  background: radial-gradient(circle, rgba(236,72,153,0.15), transparent);
  top: 50%; left: 50%; transform: translate(-50%, -50%);
}

/* ═══════════════════════════════════════════════════════════ */
/* App 容器                                                    */
/* ═══════════════════════════════════════════════════════════ */
.app {
  max-width: 1024px;
  margin: 0 auto;
  height: 100vh;
  display: flex;
  flex-direction: column;
  position: relative;
  z-index: 1;
}

/* ═══════════════════════════════════════════════════════════ */
/* 毛玻璃 Header                                               */
/* ═══════════════════════════════════════════════════════════ */
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 28px;
  margin: 12px 16px 0;
  background: var(--glass-bg-strong);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--glass-shadow);
  flex-shrink: 0;
  z-index: 100;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-primary);
  text-decoration: none;
  font-weight: 700;
}
.logo-icon { font-size: 1.5em; }
.logo-text {
  font-size: 1.15em;
  background: linear-gradient(135deg, var(--accent-1), var(--accent-2));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.subtitle {
  font-size: 0.78em;
  color: var(--text-muted);
  padding-left: 10px;
  border-left: 1px solid rgba(0,0,0,.1);
}

.header-nav {
  display: flex;
  gap: 4px;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--text-secondary);
  text-decoration: none;
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  font-size: 0.9em;
  font-weight: 500;
  transition: all 0.2s;
}
.nav-link:hover {
  background: rgba(99, 102, 241, 0.08);
  color: var(--accent-1);
}
.nav-link.router-link-active {
  background: linear-gradient(135deg, rgba(99,102,241,0.12), rgba(168,85,247,0.12));
  color: var(--accent-1);
  font-weight: 600;
}
.nav-icon { font-size: 1.1em; }

/* ═══════════════════════════════════════════════════════════ */
/* 主内容区                                                    */
/* ═══════════════════════════════════════════════════════════ */
.main-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: 12px 16px 16px;
}

/* ═══════════════════════════════════════════════════════════ */
/* 页面切换动画                                                */
/* ═══════════════════════════════════════════════════════════ */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.fade-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.fade-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* ═══════════════════════════════════════════════════════════ */
/* 全局组件样式                                                */
/* ═══════════════════════════════════════════════════════════ */
.glass-card {
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  -webkit-backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--glass-shadow);
}

.btn-primary {
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
.btn-primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(99, 102, 241, 0.4);
}
.btn-primary:active { transform: translateY(0); }
.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.input-glass {
  width: 100%;
  padding: 12px 18px;
  background: var(--glass-bg);
  backdrop-filter: blur(10px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-sm);
  font-size: 1em;
  color: var(--text-primary);
  outline: none;
  transition: all 0.2s;
}
.input-glass:focus {
  border-color: var(--accent-1);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.12);
}
.input-glass::placeholder { color: var(--text-muted); }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(0,0,0,.12); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,.2); }

/* Markdown content inside glass */
.markdown-body h2 {
  font-size: 1.15em;
  margin: 16px 0 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(0,0,0,.06);
}
.markdown-body h3 { font-size: 1.05em; margin: 12px 0 6px; }
.markdown-body table {
  border-collapse: collapse;
  width: 100%;
  margin: 10px 0;
  font-size: 0.9em;
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.markdown-body th, .markdown-body td {
  border: 1px solid rgba(0,0,0,.06);
  padding: 8px 12px;
  text-align: left;
}
.markdown-body th { background: rgba(99, 102, 241, 0.06); font-weight: 600; }
.markdown-body blockquote {
  border-left: 3px solid var(--accent-1);
  padding: 8px 16px;
  margin: 10px 0;
  color: var(--text-secondary);
  background: rgba(99, 102, 241, 0.04);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.markdown-body pre {
  background: rgba(0,0,0,.03);
  padding: 14px;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  font-size: 0.84em;
}
.markdown-body code {
  font-size: 0.9em;
  background: rgba(99, 102, 241, 0.08);
  padding: 2px 7px;
  border-radius: 4px;
  color: var(--accent-1);
}
</style>
