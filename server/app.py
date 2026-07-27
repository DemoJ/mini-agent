"""FastAPI 应用工厂。"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.constants import WEB_DIR
from server.routes import register_routes


def create_app() -> FastAPI:
    app = FastAPI(title="mini-agent WebUI")
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    register_routes(app)
    return app


app = create_app()
