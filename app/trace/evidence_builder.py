"""
决策溯源构建器 — EvidenceBuilder。

为关键结论生成依据链 + Markdown 摘要。

Usage:
    from app.trace.evidence_builder import EvidenceBuilder
    eb = EvidenceBuilder()
    trace = eb.build("推荐4.2m货车", [...])
    markdown = eb.to_markdown(trace)
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


class EvidenceBuilder:
    """构建决策溯源证据链。"""

    _counter: int = 0

    @classmethod
    def _next_id(cls, prefix: str = "ev") -> str:
        cls._counter += 1
        return f"{prefix}_{cls._counter:04d}"

    def build_tool_evidence(self, tool_name: str, result: dict, interpretation: str = "") -> dict:
        """构建工具调用证据。"""
        return {
            "evidence_id": self._next_id("tool"),
            "type": "tool_result",
            "tool_name": tool_name,
            "content": interpretation or f"工具 {tool_name} 返回结果",
            "raw_summary": str(result)[:200],
            "confidence": 0.90,
        }

    def build_rag_evidence(self, doc_id: str, content: str, score: float, authority: str = "unknown") -> dict:
        """构建 RAG 检索证据。"""
        conf = score
        if authority == "mandatory_standard":
            conf = min(conf + 0.05, 0.98)
        elif authority == "llm_generated":
            conf = max(conf - 0.05, 0.50)
        return {
            "evidence_id": self._next_id("rag"),
            "type": "rag_reference",
            "content": content[:300],
            "source_ref": doc_id,
            "confidence": round(conf, 2),
            "authority_level": authority,
        }

    def build_calc_evidence(self, formula: str, inputs: dict, result: Any) -> dict:
        """构建计算规则证据。"""
        return {
            "evidence_id": self._next_id("calc"),
            "type": "calculation_rule",
            "content": f"{formula} = {result} (输入: {inputs})",
            "formula": formula,
            "result": result,
            "confidence": 0.85,
        }

    def build_trace(
        self,
        conclusion: str,
        evidence_list: list[dict],
        confidence: str = "medium",
        uncertainty_notes: list[dict] | None = None,
    ) -> dict:
        """组装完整决策溯源。"""
        trace_id = hashlib.md5(
            f"{conclusion}{datetime.now().isoformat()}".encode()
        ).hexdigest()[:12]

        return {
            "conclusion_id": f"conc_{trace_id}",
            "trace_id": trace_id,
            "conclusion": conclusion,
            "confidence": confidence,
            "evidence_list": evidence_list,
            "uncertainty_notes": uncertainty_notes or [],
            "created_at": datetime.now().isoformat(),
        }

    def to_markdown_summary(self, trace: dict) -> str:
        """
        输出 Markdown 摘要 + <details> 折叠完整溯源。

        用途：嵌入 Agent 最终响应中。
        """
        conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}
        emoji = conf_emoji.get(trace.get("confidence", "medium"), "⚪")
        conclusion = trace.get("conclusion", "")

        # 摘要
        lines = [
            f"> **{emoji} 为什么？** {conclusion[:100]}",
            "",
        ]

        # 证据表格
        lines.append("<details>")
        lines.append("<summary>📎 查看完整决策依据</summary>")
        lines.append("")
        lines.append("| 来源 | 内容 | 置信度 |")
        lines.append("|------|------|--------|")

        for ev in trace.get("evidence_list", [])[:8]:
            etype = ev.get("type", "?")
            type_label = {"tool_result": "🔧 工具", "rag_reference": "📚 知识库", "calculation_rule": "📐 计算"}.get(etype, "❓ 其他")
            content = ev.get("content", "")[:80]
            conf = ev.get("confidence", 0)
            lines.append(f"| {type_label} | {content} | {conf:.0%} |")

        lines.append("</details>")

        # 不确定性
        for note in trace.get("uncertainty_notes", []):
            lines.append(f"> ⚠️ {note.get('field', '')}: {note.get('reason', '')}")

        return "\n".join(lines)

    def extract_traces_from_state(self, state: dict) -> list[str]:
        """从 moving_state + renovation_state 中自动提取关键结论摘要。"""
        traces: list[str] = []
        ms = state.get("moving_state", {})

        if ms.get("vehicle_recommendation"):
            v = ms["vehicle_recommendation"]
            traces.append(f"🚛 推荐车型: {v.get('type', '?')} (容量 {v.get('capacity_m3', '?')}m³)")

        if ms.get("freight_estimate"):
            f = ms["freight_estimate"]
            pr = f.get("price_range", {})
            traces.append(f"💰 运费: ¥{pr.get('min_yuan', '?')} - ¥{pr.get('max_yuan', '?')}")

        rs = state.get("renovation_state", {})
        if rs.get("budget_allocation"):
            ba = rs["budget_allocation"]
            hard = ba.get("hard_fixture", {}).get("amount", 0)
            soft = ba.get("soft_furnishing", {}).get("amount", 0)
            traces.append(f"📊 预算: 硬装¥{hard:,} + 软装¥{soft:,}")

        if rs.get("construction_phases"):
            total_d = sum(p.get("duration_days", 0) for p in rs["construction_phases"])
            traces.append(f"📅 工期: 预计 {total_d} 天")

        return traces
