"""
反馈 API — POST /feedback。

用户评分/纠正 → 实时写入 ChromaDB + PostgreSQL 待审核表。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["Feedback"])
logger = logging.getLogger(__name__)


class FeedbackRequest(BaseModel):
    session_id: str = Field(...)
    target_type: str = Field(..., pattern="^(conclusion|rag_document|tool_result)$")
    target_id: str = Field(...)
    rating: str = Field(..., pattern="^(helpful|not_helpful|correction)$")
    note: str | None = Field(None, description="correction 时必填：用户提供的纠正信息")


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """提交用户反馈。correction → 写入 ChromaDB 知识库（热更新）。"""
    logger.info(f"Feedback: type={req.target_type}, rating={req.rating}, id={req.target_id}")

    action = "logged"

    # ── helpful/not_helpful：更新已有文档评分 ──
    if req.rating in ("helpful", "not_helpful"):
        try:
            await _update_doc_score(req.target_id, req.rating)
            action = "score_updated"
        except Exception as e:
            logger.warning(f"Doc score update failed: {e}")

    # ── correction：写入新文档到 ChromaDB ──
    if req.rating == "correction" and req.note:
        try:
            doc_id = await _add_correction_doc(
                user_feedback=req.note,
                target_id=req.target_id,
                session_id=req.session_id,
            )
            action = "pending_review"
            logger.info(f"Correction doc added: {doc_id}")
        except Exception as e:
            logger.error(f"Failed to add correction doc: {e}")
            return {"success": False, "message": f"写入知识库失败: {e}"}

    return {
        "success": True,
        "action": action,
        "message": _action_message(action),
    }


async def _update_doc_score(doc_id: str, rating: str) -> None:
    """更新已有文档的 feedback_score。"""
    from app.rag.chroma_client import KnowledgeBaseManager
    manager = KnowledgeBaseManager()
    collection = manager.get_or_create_collection()

    try:
        existing = collection.get(ids=[doc_id], include=["metadatas"])
        if existing and existing.get("metadatas") and existing["metadatas"][0]:
            meta = dict(existing["metadatas"][0])
            old_score = meta.get("feedback_score", 0.5)
            old_count = meta.get("feedback_count", 0)
            old_helpful = meta.get("helpful_count", 0)

            new_count = old_count + 1
            new_helpful = old_helpful + (1 if rating == "helpful" else 0)
            # 贝叶斯平均
            new_score = (new_helpful + 5 * 0.5) / (new_count + 5)
            meta["feedback_score"] = round(new_score, 4)
            meta["feedback_count"] = new_count
            meta["helpful_count"] = new_helpful

            collection.update(ids=[doc_id], metadatas=[meta])
            logger.info(f"Updated doc {doc_id} score: {old_score:.3f} → {new_score:.3f}")
    except Exception as e:
        logger.warning(f"Doc {doc_id} not found in ChromaDB: {e}")


async def _add_correction_doc(user_feedback: str, target_id: str, session_id: str) -> str:
    """将用户纠正作为新文档写入 ChromaDB（增��热更新，无需重启）。"""
    from app.rag.chroma_client import KnowledgeBaseManager
    manager = KnowledgeBaseManager()
    collection = manager.get_or_create_collection()

    doc_id = f"user_correction_{uuid.uuid4().hex[:12]}"
    content = f"[用户纠正] 针对文档 {target_id} 的补充/修正:\n{user_feedback}"

    metadata = {
        "source": "user_feedback",
        "authority_level": "user_contributed",
        "category": "user_correction",
        "subcategory": "用户纠正",
        "tags": ["用户纠正", "待审核"],
        "feedback_score": 0.5,
        "feedback_count": 0,
        "helpful_count": 0,
        "needs_review": True,
        "parent_doc_id": target_id,
        "session_id": session_id,
        "created_at": datetime.now().isoformat(),
    }

    collection.add(documents=[content], metadatas=[metadata], ids=[doc_id])
    logger.info(f"Correction doc {doc_id} added to ChromaDB (pending review)")

    # 同时写入 PostgreSQL 待审核表
    try:
        await _write_pending_review(doc_id, user_feedback, target_id, session_id)
    except Exception as e:
        logger.warning(f"Pending review PG write failed (non-critical): {e}")

    return doc_id


async def _write_pending_review(doc_id: str, content: str, target_id: str, session_id: str) -> None:
    """写入 PostgreSQL pending_review 表。"""
    import asyncpg
    import json
    from app.config import get_config
    cfg = get_config()
    conn = await asyncpg.connect(cfg.postgres_dsn, timeout=3)
    try:
        # 确保表存在
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_review (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                chroma_doc_id VARCHAR(64) NOT NULL,
                content TEXT NOT NULL,
                target_doc_id VARCHAR(64),
                session_id VARCHAR(64),
                status VARCHAR(16) DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute(
            "INSERT INTO pending_review (chroma_doc_id, content, target_doc_id, session_id) VALUES ($1, $2, $3, $4)",
            doc_id, content, target_id, session_id,
        )
    finally:
        await conn.close()


def _action_message(action: str) -> str:
    return {
        "score_updated": "文档评分已更新",
        "pending_review": "纠正内容已写入知识库，待人工审核后正式生效",
        "logged": "反馈已记录",
    }.get(action, "反馈已记录")
