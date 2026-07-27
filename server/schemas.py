"""API 请求 / 响应模型。"""

from typing import Any

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    file_ids: list[str] = []  # 用户上传文件的 file_id 列表


class ConfigRequest(BaseModel):
    api: dict[str, Any]
    agent: dict[str, Any]


class SkillInstallRequest(BaseModel):
    url: str
    name: str | None = None
    force: bool = False
    target_dir: str | None = None


class SkillUpdateRequest(BaseModel):
    name: str


class SkillDeleteRequest(BaseModel):
    name: str
