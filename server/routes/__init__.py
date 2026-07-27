"""API 路由模块。"""

from fastapi import FastAPI

from server.routes import chat, config, files, pages, skills


def register_routes(app: FastAPI) -> None:
    app.include_router(pages.router)
    app.include_router(chat.router)
    app.include_router(config.router)
    app.include_router(files.router)
    app.include_router(skills.router)
