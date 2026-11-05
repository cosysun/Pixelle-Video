# 部署和存储

适用于本次精简后的 Pixelle Studio。运行一个 API 进程和串行后台任务；前端由 API 或可选 NGINX 托管。

## 配置

复制 `.env.example` 为 `.env`，填写账号、COS 桶、地域、凭据。域名和证书在已有 NGINX 中配置。已有的 `.env` 不会被脚本覆盖。不要在聊天或版本库提交凭据。

```bash
uv sync --extra dev
```

在 `.env` 中直接填写登录账号和密码，用单引号包裹密码。例如：

```dotenv
PIXELLE_USERNAME=admin
PIXELLE_PASSWORD='在这里填写你的登录密码'
```

登录页面输入相同的账号和密码。修改后重启应用使其生效；Docker Compose 部署执行 `docker compose up -d app` 重新创建使用新配置的容器。应用启动时在内存中生成校验值，登录密码不写入数据库、不返回页面。`.env` 已被 Git 忽略。

应用根据 NGINX 保留的 `Host` 自动校验浏览器写请求的 Origin，默认使用 HTTPS 来源和 Secure 登录 Cookie。无需填写公共 URL、Cookie 开关或可信代理网段。仅本机直接 HTTP 开发时，额外设置 `PIXELLE_COOKIE_SECURE=false`；此时校验 HTTP 来源。

- `PIXELLE_PORT`：Docker 宿主机映射端口，默认 `8000`，容器内应用固定监听 `8000`。直接 Python 启动通过 `--port` 指定端口，此变量不影响 Python 命令。
- `PIXELLE_BIND_HOST`：Docker 宿主机绑定地址，默认 `127.0.0.1`。另一台机器的 NGINX 通过内网连接时，填写应用宿主机的内网 IP；它不是 NGINX 的地址。直接 Python 启动通过 `--host` 指定地址。
- `COS_BUCKET`：包含 APPID 的完整桶名称；`COS_REGION` 如 `ap-guangzhou`。
- `COS_SECRET_ID`、`COS_SECRET_KEY`：服务器凭据。使用 CAM 控制台创建的长期密钥时，COS 只需配置桶名称、地域和这两个密钥，`COS_SESSION_TOKEN` 留空或省略。
- `COS_SESSION_TOKEN`：本项目定义的可选环境变量，对应腾讯云 COS Python SDK 的 `Token` 参数。仅使用 STS 临时凭据时，填写临时凭据返回的会话令牌，并配套填写临时 SecretId/SecretKey；到期后在运行环境更新并重启。
- `COS_PREFIX`：应用独占前缀，默认 `pixelle`。运行中不要直接修改，否则已有对象引用无法找到。
- `PIXELLE_DATABASE`：SQLite 元数据路径；数据库包含服务商密钥，仅备份到受控位置。
- `PIXELLE_TEMP_DIR`：临时处理目录；任务结束后清理，服务器崩溃遗留文件随容器 tmpfs 消失。

COS 桶建议使用私有读写。共享桶可沿用既有桶权限，本项目写入的对象显式设置 private，浏览器直传的签名也要求 `x-cos-acl: private`。给服务器账号限定应用前缀的 GetObject、PutObject、PutObjectACL、DeleteObject、HeadObject、ListBucket 权限。为浏览器直传配置 CORS：允许来源仅为应用域名，方法 PUT/GET/HEAD，允许 Content-Type 和 x-cos-acl 请求头，暴露 ETag。本机联调临时加入本机来源。浏览器获取短期签名链接，密钥由服务器持有；NGINX 不代理作品数据。

## 资源导入与旧作品迁移

```bash
uv run python -m pixelle_video.platform.cli resources --root .
uv run python -m pixelle_video.platform.cli history --root output
```

内置模板源文件位于 `templates/`，随应用镜像发布，运行时直接读取并复制到临时工作区，不依赖 COS。内置效果图位于 `frontend/public/template-previews/`，随 Web 发布，列表、已选小图和默认大预览直接加载静态图片。更新模板后先生成效果图，再构建发布；浏览器刷新读取新版本，无需循环生成。

```bash
python -m pixelle_video.platform.cli template-covers --root .
npm run build --prefix frontend
```

效果图按模板内容版本缓存，未变化的模板跳过生成，变化后使用新的文件名。资源命令只把背景音乐、`data/templates` 和 `data/bgm` 的自定义资源上传 COS；内置模板只读取本地目录。作品素材、用户上传和个人文案/参数预览继续使用 COS。

历史迁移上传每个被引用的文件，上传成功后发布 COS 清单和 SQLite 目录，再读取清单验证。已有作品跳过；缺失素材逐项报告且返回非零退出码，修复后可重跑。原始 `output` 不会被删除。数据库目录还未发布的失败上传对象可能残留，归属相同作品前缀；迁移成功后可通过作品删除一并清理。

旧工作流仅保留为历史配置记录。播放、下载和无需生成素材的编辑可继续使用；重生成和恢复需要重新选择直连模型/配音。旧资源模板应先完成导入。模型凭据可以首次从现有 `config.yaml` 导入，之后在页面设置中保存到数据库；不再修改原 YAML。

## 本地启动

```bash
npm ci --prefix frontend
npm run build --prefix frontend
uv run python -m api.app
```

API 默认绑定 `127.0.0.1:8000`，同时提供构建后的页面。开发前端执行 `npm run dev --prefix frontend`，通过 Vite 代理访问 API。所有业务接口需要登录；只有 `/health` 是公开健康检查。

## 已有 NGINX：证书由现有网关管理

浏览器通过 HTTPS 访问现有 NGINX，NGINX 通过内网 HTTP 转发到应用。应用容器同时提供前端和 API，不加载证书，也不需要启动项目自带 NGINX。

此部署可以省略 `PIXELLE_CERT_DIR`、`PIXELLE_HTTP_PORT`、`PIXELLE_HTTPS_PORT`，它们只用于自带 NGINX 的证书挂载和宿主机端口映射。

下面的应用宿主机内网 IP `10.0.0.20` 仅作示例，替换为实际值；域名在 NGINX 中配置：

```dotenv
PIXELLE_PORT=8000
PIXELLE_BIND_HOST=10.0.0.20
```

如果 NGINX 安装在同一台宿主机，省略 `PIXELLE_BIND_HOST`，转发到 `http://127.0.0.1:8000`。如果 NGINX 自身运行在容器内，其 `127.0.0.1` 指向 NGINX 容器，需通过已有 Docker 网络或宿主机的可达地址连接应用。

将 `deploy/nginx/existing-proxy.conf.example` 中的 `location /` 合并到现有 HTTPS `server` 块并替换上游地址，保留原有证书和域名配置。页面与 `/api/` 一起转发到应用，必须通过 `proxy_set_header Host $http_host` 保留浏览器请求的域名及端口。部署后访问域名根路径。

默认不读取代理请求头，登录尝试限流按代理的连接 IP 计算；单账号部署下同一代理后的访问共享限流计数。需要区分客户端 IP 时，可另外设置 `PIXELLE_TRUSTED_PROXIES` 为实际可信代理 IP，同时由 NGINX 覆盖转发头；这是可选能力，不影响域名校验或 HTTPS 登录。

```bash
docker compose up -d --build app
```

仅允许 NGINX 通过内网访问应用端口，按实际网络设置安全组/防火墙。登录账号密码、COS 和数据库配置沿用前文。

## 项目自带 NGINX（可选）

```bash
# 已有反向代理时，仅启动应用
# 应用映射到宿主机 loopback，网关代理 127.0.0.1:8000。
docker compose up -d --build app

# 使用配套 NGINX 时
# PIXELLE_CERT_DIR 下放置已有 fullchain.pem、privkey.pem。
docker compose --profile edge up -d --build

# 初始化 COS 资源（容器命令同样读 .env）
docker compose exec app python -m pixelle_video.platform.cli resources
```

只有 SQLite 使用 `platform-data` 持久卷，任务临时文件使用 tmpfs，没有作品输出卷。NGINX HTTPS 入口做静态托管和 API 转发；同域请求、HttpOnly 登录 Cookie。HTTP 自动跳转 HTTPS，已有证书只读挂载，证书更新后 reload NGINX。证书的获取和续期沿用现有机制。

不要启用多 Uvicorn worker：当前持久任务队列设计为单进程，后续并发扩展需引入跨进程领取与锁。

## 运维

- 新任务和编辑由持久队列执行；切换页面/刷新不停止。单作品互斥，默认串行，取消在处理阶段之间生效，正在发送的服务商请求不能远程撤销。
- 重启将运行任务标记中断，手动恢复，避免自动重复调用付费模型。
- 作品素材、检查点和修订使用不可变 COS 对象；完成后切换目录指针。失败编辑保留已发布版本。删除失败可重试；删除作品前缀不影响共享模板和音乐。
- 临时试听、模板预览和未确认上传保留一天，后台每小时清理；可手动运行 `python -m pixelle_video.platform.cli cleanup`。作品修订保留至作品删除，暂不自动过期。
- 同时备份 SQLite 及 COS 前缀。SQLite 在容器运行期间使用 SQLite backup API 制作一致快照，避免只复制 WAL 模式主文件。

## 检查命令

```bash
uv run pytest tests/platform/platform_test.py -q
npm run build --prefix frontend
cd frontend
npx playwright install chromium webkit
npm test
```

真实 COS 上传/跨域/签名、服务商调用、HTTPS 证书和容器启动需要已配置环境做端到端验收。

## 测试范围

默认 pytest 运行当前平台测试；兼容的历史管理/图片尺寸回归已另外执行。项目中用户原有 `tests/web` 和依赖本地 `PersistenceService(output_dir=...)` 的旧测试文件原样保留，属于已退役界面和本地存储契约；显式执行这些旧测试会失败，不能作为新 COS 平台验收。新平台测试覆盖对应的完整编辑链路和真实 FFmpeg/Chromium 合成。
