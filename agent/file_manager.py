"""
文件管理模块
============
管理用户上传的文件和 Agent 交付给用户的文件。

- uploads/ : 用户从 WebUI 上传的文件存放目录
- outputs/ : Agent 通过 deliver_file 工具交付的文件存放目录

使用内存注册表（file_id -> 文件信息）管理文件元数据。
file_id 为 12 位 hex，文件以 {file_id}__{原名} 形式存储，便于脱离注册表也能定位。
"""

import os
import shutil
import uuid
from pathlib import Path
from typing import Any


class FileManager:
    """文件存储与注册表管理。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.uploads_dir = self.base_dir / "uploads"
        self.outputs_dir = self.base_dir / "outputs"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        # file_id -> {file_id, filename, stored_path, size, type, description?}
        self._registry: dict[str, dict[str, Any]] = {}

    # --------------------------------------------------------
    # 用户上传
    # --------------------------------------------------------

    def save_upload(self, content: bytes, filename: str) -> dict[str, Any]:
        """保存用户上传的文件，返回文件信息字典。"""
        file_id = uuid.uuid4().hex[:12]
        safe_name = self._sanitize(filename)
        stored_name = f"{file_id}__{safe_name}"
        stored_path = self.uploads_dir / stored_name
        with open(stored_path, "wb") as f:
            f.write(content)
        size = os.path.getsize(stored_path)
        info: dict[str, Any] = {
            "file_id": file_id,
            "filename": filename,
            "stored_path": str(stored_path),
            "size": size,
            "type": "upload",
        }
        self._registry[file_id] = info
        return info

    # --------------------------------------------------------
    # Agent 交付
    # --------------------------------------------------------

    def deliver_file(self, src_path: str, description: str = "") -> dict[str, Any]:
        """将 Agent 生成的文件复制到 outputs/ 目录，注册到注册表。

        Args:
            src_path: 源文件路径（绝对路径或相对工作目录的路径）
            description: 对文件的描述（可选）

        Returns:
            成功: {ok: True, file_id, filename, stored_path, size, description}
            失败: {error: "..."}
        """
        src = Path(src_path)
        if not src.exists():
            return {"error": f"文件不存在: {src_path}"}
        if not src.is_file():
            return {"error": f"路径不是文件: {src_path}"}

        file_id = uuid.uuid4().hex[:12]
        safe_name = self._sanitize(src.name)
        stored_name = f"{file_id}__{safe_name}"
        stored_path = self.outputs_dir / stored_name
        try:
            shutil.copy2(str(src), str(stored_path))
        except Exception as e:
            return {"error": f"复制文件失败: {e}"}

        size = os.path.getsize(stored_path)
        info: dict[str, Any] = {
            "ok": True,
            "file_id": file_id,
            "filename": src.name,
            "stored_path": str(stored_path),
            "size": size,
            "type": "output",
            "description": description,
        }
        self._registry[file_id] = {k: v for k, v in info.items() if k != "ok"}
        return info

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        """按 file_id 查询文件信息。"""
        return self._registry.get(file_id)

    def list_files(self) -> list[dict[str, Any]]:
        """列出所有已注册的文件。"""
        return list(self._registry.values())

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------

    @staticmethod
    def _sanitize(name: str) -> str:
        """清理文件名，只保留安全字符。"""
        safe = "".join(c for c in name if c.isalnum() or c in "._-()")
        return safe or "file"


# ============================================================
# 模块级单例
# ============================================================

_file_manager: FileManager | None = None


def init_file_manager(base_dir: str | Path) -> FileManager:
    """初始化全局 FileManager 单例。

    幂等：如果已存在实例则直接返回，不覆盖——避免 Agent 重建时
    丢失此前上传接口已注册的文件记录。
    """
    global _file_manager
    if _file_manager is None:
        _file_manager = FileManager(Path(base_dir))
    return _file_manager


def get_file_manager() -> FileManager:
    """获取全局 FileManager 单例。未初始化时抛出 RuntimeError。"""
    if _file_manager is None:
        raise RuntimeError("FileManager 未初始化，请先调用 init_file_manager()")
    return _file_manager
