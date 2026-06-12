-- ═══════════════════════════════════════════════════════════
-- MoveRenovateAgent — 数据库初始化脚本
-- ═══════════════════════════════════════════════════════════

-- pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 用户画像 ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_profile (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL UNIQUE,
    preferences         JSONB NOT NULL DEFAULT '{}',
    preference_history  JSONB NOT NULL DEFAULT '[]',
    total_conversations INT DEFAULT 0,
    last_updated        TIMESTAMPTZ DEFAULT NOW(),
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_profile_user ON user_profile(user_id);

-- ── 历史方案 ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS historical_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    plan_version    INT NOT NULL DEFAULT 1,
    plan_snapshot   JSONB NOT NULL,
    summary         TEXT,
    user_feedback   JSONB,
    tokens_used     INT,
    tools_called    TEXT[],
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_plans_user_time ON historical_plans(user_id, created_at DESC);

-- ── 会话状态（风险 1 优化：完整状态存 PG） ────────────────
CREATE TABLE IF NOT EXISTS session_states (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    VARCHAR(64) NOT NULL,
    version       INT NOT NULL DEFAULT 0,
    state_json    BYTEA NOT NULL,
    checksum      VARCHAR(16) NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(session_id, version)
);
CREATE INDEX IF NOT EXISTS idx_ss_session_version ON session_states(session_id, version DESC);

-- ── 决策溯源归档（风险 8 优化） ──────────────────────────
CREATE TABLE IF NOT EXISTS decision_trace_archive (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          VARCHAR(64) NOT NULL,
    conversation_round  INT NOT NULL,
    trace_id            VARCHAR(32) NOT NULL UNIQUE,
    conclusion          TEXT NOT NULL,
    evidence_list       JSONB NOT NULL,
    confidence          VARCHAR(16) NOT NULL,
    summary_100         VARCHAR(120) NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trace_session_round ON decision_trace_archive(session_id, conversation_round DESC);

-- ── MCP 调用历史（风险 4 优化：L1 历史缓存） ──────────────
CREATE TABLE IF NOT EXISTS mcp_call_history (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cache_key           VARCHAR(32) NOT NULL,
    tool_name           VARCHAR(64) NOT NULL,
    params_hash         VARCHAR(64) NOT NULL,
    from_city           VARCHAR(64),
    to_city             VARCHAR(64),
    total_volume_m3     FLOAT,
    vehicle_type        VARCHAR(32),
    distance_km         FLOAT,
    total_estimate_yuan FLOAT,
    success             BOOLEAN DEFAULT TRUE,
    raw_response        JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mcp_cache_key ON mcp_call_history(cache_key, created_at DESC);

-- ── 城市距离缓存（风险 4 优化：L2 距离矩阵） ──────────────
CREATE TABLE IF NOT EXISTS city_distance_cache (
    city_a          VARCHAR(64) NOT NULL,
    city_b          VARCHAR(64) NOT NULL,
    avg_distance_km FLOAT NOT NULL,
    sample_count    INT DEFAULT 0,
    last_updated    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (city_a, city_b)
);
