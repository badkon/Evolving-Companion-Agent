"""Temporary authenticated loopback ASGI server, owned by Manager, not Core."""

from dataclasses import asdict
import asyncio
import json
from pathlib import Path
import secrets
import socket
from threading import Thread
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from starlette.templating import Jinja2Templates
import uvicorn

from evolving_companion.connection_tests import test_connection
from evolving_companion.console_services import ConsoleService
from evolving_companion.manager_services import ManagerService
from evolving_companion.web_setup_services import (
    CHARACTER_FIELDS,
    FIELDS,
    SECRET_LABELS,
    WebSetupService,
)

ASSETS = Path(__file__).with_name("web")
PAGES = {
    "/": "首页",
    "/chat": "聊天管理",
    "/media": "表情与媒体",
    "/character": "角色配置",
    "/affective": "情绪与关系",
    "/memory": "记忆管理",
    "/models": "模型与服务",
    "/runtime": "运行与日志",
    "/system": "数据与系统",
}


def create_app(service: WebSetupService, token: str) -> Starlette:
    templates = Jinja2Templates(directory=ASSETS)
    console = ConsoleService(
        service.manager
        or ManagerService(
            service.env.path,
            project_root=service.project,
            environment=service.environment,
        )
    )
    session = secrets.token_urlsafe(32)
    cookie_name = "si_console_" + secrets.token_hex(8)

    def bearer(request):
        return secrets.compare_digest(
            request.headers.get("authorization", "").encode(),
            f"Bearer {token}".encode(),
        )

    def authenticated(request):
        if "authorization" in request.headers:
            return bearer(request)
        return secrets.compare_digest(
            request.cookies.get(cookie_name, "").encode(), session.encode()
        )

    async def asset(request):
        name = request.path_params["name"]
        if name not in {"setup.css", "setup.js"}:
            return JSONResponse({"message": "不存在的资源。"}, status_code=404)
        return FileResponse(ASSETS / name)

    async def login(request):
        if not bearer(request):
            return JSONResponse({"message": "访问凭证无效。"}, status_code=401)
        response = JSONResponse({"ok": True})
        # HTTP is deliberately loopback-only. Session dies with this Console server.
        response.set_cookie(cookie_name, session, httponly=True, samesite="strict")
        return response

    async def home(request: Request):
        if not authenticated(request):
            return templates.TemplateResponse(
                request, "login.html", {}, status_code=401
            )
        return templates.TemplateResponse(
            request,
            "console.html",
            {
                "page": request.url.path,
                "title": PAGES[request.url.path],
                "pages": PAGES,
                "fields": FIELDS,
                "secrets": SECRET_LABELS,
                "character_fields": CHARACTER_FIELDS,
            },
        )

    async def api(request: Request):
        if not authenticated(request):
            return JSONResponse(
                {"ok": False, "message": "访问凭证无效；请从管理器重新打开地址。"},
                status_code=401,
            )
        origin = request.headers.get("origin")
        if origin and origin != f"http://{request.headers.get('host')}":
            return JSONResponse(
                {"ok": False, "message": "不允许跨站请求。"}, status_code=403
            )
        try:
            if request.method == "GET":
                action = request.path_params["action"]
                if action == "state":
                    data = await run_in_threadpool(service.snapshot)
                elif action == "overview":
                    data = await run_in_threadpool(console.overview)
                elif action == "affective":
                    data = await run_in_threadpool(console.affective)
                elif action == "memories":
                    data = await run_in_threadpool(
                        console.memories,
                        request.query_params.get("q", ""),
                        int(request.query_params.get("offset", "0")),
                    )
                else:
                    return JSONResponse({"message": "未知操作。"}, status_code=404)
                return JSONResponse(console.clean(data))
            if request.path_params["action"] == "affective":
                return JSONResponse(
                    {"message": "情绪与关系页面仅支持只读查询。"},
                    status_code=405,
                    headers={"Allow": "GET"},
                )
            if (
                request.headers.get("content-type", "").split(";")[0]
                != "application/json"
            ):
                return JSONResponse(
                    {"ok": False, "message": "需要 JSON 请求。"}, status_code=415
                )
            body = bytearray()
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 262144:
                        return JSONResponse(
                            {"ok": False, "message": "请求过大。"}, status_code=413
                        )
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Object required")
            action = request.path_params["action"]
            if action == "save":
                result = await run_in_threadpool(service.save, payload)
            elif action == "character":
                result = await run_in_threadpool(service.save_character, payload)
            elif action == "validate":
                result = await run_in_threadpool(service.validate, payload)
            elif action in {"llm", "embedding", "reranker", "qq"}:
                values = await run_in_threadpool(
                    lambda: service.effective(service.updates(payload))
                )
                result = await run_in_threadpool(test_connection, action, values)
            elif action in {"start", "stop", "restart", "logs"}:
                result = await run_in_threadpool(getattr(console.manager, action))
            elif action == "health":
                result = await run_in_threadpool(console.manager.check, health=True)
            else:
                return JSONResponse(
                    {"ok": False, "message": "未知操作。"}, status_code=404
                )
            return JSONResponse(console.clean(asdict(result)))
        except Exception as error:
            # Never serialize validation input, raw provider errors or tracebacks.
            return JSONResponse(
                {
                    "ok": False,
                    "message": f"操作失败（{type(error).__name__}）；请检查字段格式、权限或重新加载以解决并发修改。",
                },
                status_code=400,
            )

    app = Starlette(
        routes=[
            *[Route(path, home) for path in PAGES],
            Route("/api/session", login, methods=["POST"]),
            Route("/api/{action}", api, methods=["GET", "POST"]),
            Route("/static/{name}", asset),
        ]
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    async def headers(request, call_next):
        origin = request.headers.get("origin")
        expected = f"http://{request.headers.get('host')}"
        if (origin and origin != expected) or (
            request.method == "POST" and not bearer(request) and origin != expected
        ):
            response = JSONResponse({"message": "不允许跨站请求。"}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=headers)

    return app


class WebSetupServer:
    def __init__(self, service: WebSetupService):
        self.token = secrets.token_urlsafe(32)
        self.server = uvicorn.Server(
            uvicorn.Config(
                create_app(service, self.token),
                access_log=False,
                log_config=None,
                log_level="critical",
                lifespan="off",
            )
        )
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.bind(("127.0.0.1", 0))
            self.port = self.socket.getsockname()[1]
        except BaseException:
            self.socket.close()
            raise
        self.thread = Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True
        )

    @property
    def is_running(self) -> bool:
        return (
            self.thread.is_alive()
            and self.server.started
            and not self.server.should_exit
        )

    @property
    def url(self) -> str:
        # Fragment is never sent to HTTP servers, referrers or access logs.
        return f"http://127.0.0.1:{self.port}/#{self.token}"

    def start(self) -> None:
        try:
            self.thread.start()
            deadline = time.monotonic() + 5
            while not self.server.started:
                if not self.thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Web Setup startup failed")
                time.sleep(0.02)
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        self.server.should_exit = True
        if self.thread.ident is not None:
            # Uvicorn drains in-flight saves/probes before returning; no forced kill.
            self.thread.join()
        self.socket.close()
