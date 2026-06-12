"""
Skill API — POST /skill/reload。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.skill.registry import get_skill_registry

router = APIRouter(tags=["Skill"])


class SkillReloadRequest(BaseModel):
    skill_id: str | None = Field(None, description="指定重载某个 Skill，不传则全量重载")


@router.post("/skill/reload")
async def reload_skill(req: SkillReloadRequest | None = None):
    """热加载 Skill（扫描 skills/ 目录）。"""
    registry = await get_skill_registry()
    skill_id = req.skill_id if req else None

    if skill_id:
        version = await registry.reload(skill_id)
        if version is None:
            return {"success": False, "message": f"Failed to reload {skill_id}"}
        return {
            "success": True,
            "loaded_count": 1,
            "skills": [{"skill_id": skill_id, "version": version}],
            "errors": [],
        }

    # 全量重载
    count = await registry.scan_directory("skills/")
    descs = registry.get_short_descs()
    return {
        "success": True,
        "loaded_count": count,
        "skills": [{"skill_id": d.skill_id, "display_name": d.display_name} for d in descs],
        "errors": [],
    }
