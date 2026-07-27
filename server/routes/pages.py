"""页面与静态资源。"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from server.constants import WEB_DIR

router = APIRouter()


@router.get("/")
def index():
    html = WEB_DIR / "index.html"
    if not html.exists():
        raise HTTPException(status_code=404, detail="index.html 不存在")
    return FileResponse(str(html))
