"""Skill 管理接口。"""

from fastapi import APIRouter, HTTPException

from agent.skill_manager import (
    SkillManageError,
    delete_skill,
    info_skill,
    install_skill,
    list_skills,
    update_skill,
)
from server.config_util import require_config
from server.schemas import SkillInstallRequest, SkillUpdateRequest
from server.state import rebuild_agent

router = APIRouter()


@router.get("/api/skills")
def api_list_skills():
    """列出所有已安装 skill。"""
    require_config()
    try:
        return {"skills": list_skills()}
    except SkillManageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/skills/dirs")
def api_list_skill_dirs():
    """返回已配置的 skills 目录列表（供前端安装时选择目标目录）。"""
    cfg = require_config()
    return {"dirs": [str(d) for d in cfg.agent.skills_dirs]}


@router.get("/api/skills/{name}")
def api_info_skill(name: str):
    """查询单个 skill 详情。"""
    require_config()
    try:
        return info_skill(name)
    except SkillManageError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/skills/install")
def api_install_skill(req: SkillInstallRequest):
    """从 git 仓库安装 skill。"""
    require_config()
    try:
        result = install_skill(
            req.url, name=req.name, force=req.force, target_dir=req.target_dir
        )
        rebuild_agent()
        return {"ok": True, **result}
    except SkillManageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/skills/update")
def api_update_skill(req: SkillUpdateRequest):
    """更新已安装的 skill。"""
    require_config()
    try:
        result = update_skill(req.name)
        rebuild_agent()
        return {"ok": True, **result}
    except SkillManageError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/skills/{name}")
def api_delete_skill(name: str):
    """删除已安装的 skill。"""
    require_config()
    try:
        result = delete_skill(name)
        rebuild_agent()
        return {"ok": True, **result}
    except SkillManageError as e:
        raise HTTPException(status_code=400, detail=str(e))
