# Pixelle Studio

一个专注创作的桌面与移动 Web 工作台。输入主题或已有文案，选择画面风格、直连模型、配音和音乐，生成视频；在作品库继续修改标题、模板、音乐与分镜。

当前系统保留快速创作、批量生成和完整历史编辑，移除了 Streamlit、RunningHub、ComfyUI、数字人和独立工作流页面。界面使用 React / TypeScript，后端使用 FastAPI；作品和资源全部保存在私有腾讯云 COS，SQLite 保存目录、任务、设置与会话。

## 开始使用

1. 复制 `.env.example` 为 `.env`，配置 COS，并直接填写 `PIXELLE_USERNAME` 和 `PIXELLE_PASSWORD`；域名及证书由现有 NGINX 管理。
2. `uv sync --extra dev` 安装依赖。
3. `uv run python -m pixelle_video.platform.cli resources` 将模板和音乐导入 COS。
4. `npm ci --prefix frontend`，`npm run build --prefix frontend`。
5. `uv run python -m api.app`，访问 `http://127.0.0.1:8000`；本机直接 HTTP 开发时额外设置 `PIXELLE_COOKIE_SECURE=false`。部署到已有 HTTPS NGINX 时无需设置该项或公共 URL。

在创作设置中填写 LLM、图片/视频和 MiniMax 凭据。Edge TTS 无需配音密钥。

旧历史使用 `uv run python -m pixelle_video.platform.cli history --root output` 迁移，原文件保留。生产部署支持 Docker 和可选 NGINX HTTPS 入口，已有网关可复用。

- [部署、COS、迁移和运维](docs/deployment.md)
- [架构与文章模块扩展](docs/architecture.md)
- [投资资产跟踪、账本迁移与计算口径](docs/investment.md)
- [归档改造方案](docs/plans/2026-11-05-creation-platform-redesign.md)

## 验证

```bash
uv run pytest tests/platform/platform_test.py -q
npm run build --prefix frontend
cd frontend
npx playwright install chromium webkit
npm test
```

浏览器测试中的资源、模型和作品为隔离测试数据，生产环境始终使用真实 COS 与登录配置。

Apache-2.0；保留原项目版权声明。
