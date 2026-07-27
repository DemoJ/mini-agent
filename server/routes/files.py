"""文件上传 / 下载接口。"""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from agent.file_manager import (
    get_file_manager,
    get_image_mime,
    init_file_manager,
    is_image_file,
)
from server.constants import MAX_UPLOAD_SIZE, PROJECT_ROOT

router = APIRouter()


@router.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    """接收用户上传的文件，保存到 uploads/ 目录，返回文件信息。"""
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        return JSONResponse(
            status_code=413,
            content={"error": f"文件大小超过限制（{MAX_UPLOAD_SIZE // 1024 // 1024}MB）"},
        )
    try:
        fm = get_file_manager()
    except RuntimeError:
        fm = init_file_manager(PROJECT_ROOT)
    try:
        info = fm.save_upload(content, file.filename or "upload")
        return {
            "ok": True,
            "file_id": info["file_id"],
            "filename": info["filename"],
            "size": info["size"],
            "is_image": is_image_file(info["filename"]),
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"保存文件失败: {e}"},
        )


@router.get("/api/files/{file_id}")
def api_download_file(file_id: str):
    """按 file_id 下载文件。"""
    try:
        fm = get_file_manager()
    except RuntimeError:
        raise HTTPException(status_code=404, detail="文件服务未就绪")
    info = fm.get_file(file_id)
    if info is None:
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    stored_path = Path(info["stored_path"])
    if not stored_path.exists():
        raise HTTPException(status_code=404, detail="文件已被删除")
    media_type = "application/octet-stream"
    if is_image_file(info["filename"]):
        media_type = get_image_mime(info["filename"])
    return FileResponse(
        str(stored_path),
        filename=info["filename"],
        media_type=media_type,
    )
