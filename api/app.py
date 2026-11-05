"""Authenticated creation API and optional frontend hosting behind any reverse proxy."""

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from api.schemas.studio import (
    LoginRequest,
)
from pixelle_video.platform.security import hash_password, verify_password

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def create_app(platform=None):
    password_hash = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal password_hash
        password = os.getenv("PIXELLE_PASSWORD", "")
        if not os.getenv("PIXELLE_USERNAME") or not password:
            raise RuntimeError("请设置 PIXELLE_USERNAME 和 PIXELLE_PASSWORD")
        password_hash = hash_password(password)
        del password
        if platform is None:
            from pixelle_video.platform.runtime import Platform

            app.state.platform = Platform()
        await app.state.platform.start()
        try:
            yield
        finally:
            await app.state.platform.stop()
            if platform is None:
                app.state.platform.database.close()

    app = FastAPI(
        title="Pixelle Studio", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    if platform is not None:
        app.state.platform = platform
    failures = defaultdict(deque)
    cookie_secure = os.getenv("PIXELLE_COOKIE_SECURE", "true").lower() == "true"

    def service(request):
        return request.app.state.platform

    async def authenticated(request: Request):
        token = request.cookies.get("pixelle_session", "")
        if not token:
            raise HTTPException(401, "请登录后继续")
        hashed = hashlib.sha256(token.encode()).hexdigest()
        row = service(request).database.one("SELECT expires FROM sessions WHERE token=?", (hashed,))
        if not row or row["expires"] < time.time():
            raise HTTPException(401, "登录已过期，请重新登录")
        return True

    @app.middleware("http")
    async def security_headers(request, call_next):
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                # NGINX retains Host while HTTPS terminates at the gateway.
                # Cookie security determines the browser-facing protocol, so
                # internal HTTP forwarding needs no public URL/proxy override.
                scheme = "https" if cookie_secure else request.url.scheme
                expected = f"{scheme}://{request.headers.get('host', '')}"
                try:
                    supplied, target = urlsplit(origin), urlsplit(expected)
                    valid = (
                        supplied.scheme == target.scheme
                        and supplied.hostname == target.hostname
                        and supplied.hostname is not None
                        and (supplied.port or (443 if supplied.scheme == "https" else 80))
                        == (target.port or (443 if target.scheme == "https" else 80))
                        and supplied.username is None
                        and supplied.password is None
                        and supplied.path in {"", "/"}
                        and not supplied.query
                        and not supplied.fragment
                        and not target.path
                        and not target.query
                        and not target.fragment
                        and target.username is None
                        and target.password is None
                    )
                except ValueError:
                    valid = False
                if not valid:
                    return Response("Invalid origin", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/auth/login")
    async def login(body: LoginRequest, request: Request, response: Response):
        address = request.client.host
        attempts = failures[address]
        while attempts and attempts[0] < time.time() - 300:
            attempts.popleft()
        if len(attempts) >= 8:
            raise HTTPException(429, "尝试过于频繁，请五分钟后重试")
        valid_password = verify_password(body.password, password_hash or "")
        if not valid_password or not hmac.compare_digest(
            body.username.encode(), os.getenv("PIXELLE_USERNAME", "").encode()
        ):
            attempts.append(time.time())
            raise HTTPException(401, "账号或密码不正确")
        failures.pop(address, None)
        token = secrets.token_urlsafe(48)
        db = service(request).database
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        db.execute(
            "INSERT INTO sessions VALUES(?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), time.time() + 604800),
        )
        response.set_cookie(
            "pixelle_session",
            token,
            max_age=604800,
            httponly=True,
            secure=cookie_secure,
            samesite="strict",
            path="/",
        )
        return {"username": body.username}

    @app.get("/api/auth/me", dependencies=[Depends(authenticated)])
    async def me():
        return {"username": os.getenv("PIXELLE_USERNAME")}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response):
        token = request.cookies.get("pixelle_session", "")
        service(request).database.execute(
            "DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),)
        )
        response.delete_cookie("pixelle_session", path="/")
        return {"success": True}

    from fastapi import APIRouter

    api = APIRouter(prefix="/api", dependencies=[Depends(authenticated)])

    from api.routes import previews, shared, video, works

    for module in (shared, previews, video, works):
        module.mount(api, service)

    app.include_router(api)
    dist = ROOT / "frontend" / "dist"

    @app.get("/{path:path}")
    async def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        file = (dist / path).resolve()
        if file != dist.resolve() and dist.resolve() not in file.parents:
            raise HTTPException(404)
        if file.is_file():
            return FileResponse(file)
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        raise HTTPException(404, "请先构建前端或使用 Vite 开发服务器")

    return app


app = create_app()

if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    trusted_proxies = os.getenv("PIXELLE_TRUSTED_PROXIES")
    uvicorn.run(
        "api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        proxy_headers=bool(trusted_proxies),
        forwarded_allow_ips=trusted_proxies or "127.0.0.1",
    )
