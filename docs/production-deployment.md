# work.dazhao.fun 部署记录

## 部署位置

- SSH：`ubuntu@124.220.180.133`。
- 操作系统：Ubuntu 26.04 LTS，x86_64；约 3.6 GiB 内存，49 GiB 系统盘，2 GiB swap。
- 项目目录：`/opt/pixelle-studio`。
- NGINX：宿主机服务，转发到 `127.0.0.1:8000`，与应用同机。
- 应用：Docker Compose 单实例，使用仓库 `docker-compose.yml` 和 `deploy/docker-compose.production.yml` 的生产覆盖配置；服务器覆盖文件放在项目目录根部。
- 域名：`work.dazhao.fun`，已解析到服务器公网 IP。

## 证书

证书与私钥已匹配，证书有效期为 2026-11-05 至 2027-02-02。仅使用用户提供证书包中的证书链和私钥，不执行包内指令。证书存于 `/etc/nginx/certs/work.dazhao.fun/fullchain.pem`，私钥存于同目录 `privkey.pem`，私钥权限 0600，归 root 所有。

站点配置为 `/etc/nginx/sites-available/work.dazhao.fun`，启用链接位于 `sites-enabled`。仓库副本为 `deploy/nginx/work.dazhao.fun.conf`；HTTP 跳转到 HTTPS，页面与 API 一起转发并保留 Host。更新证书后执行 `sudo nginx -t && sudo systemctl reload nginx`。

## 配置及存储

`.env` 通过 SSH 加密传输，权限 0600，不进入镜像或版本库。原有模型 `config.yaml` 以只读挂载方式供首次初始化数据库读取，归容器 UID 10001 所有，权限 0600；后续页面保存设置到 SQLite。

SQLite 使用 Docker `platform-data` 持久卷，数据库路径 `/data/platform.sqlite3`，宿主机卷名 `pixelle-studio_platform-data`，目录与数据库归 UID 10001 所有。任务临时目录使用最大 1536 MiB 的 tmpfs；`TMPDIR=/tmp/pixelle` 让预览和配音也使用内存工作区，结束后清理。媒体、模板、背景音乐、作品清单与分镜存入 COS，历史源文件保留在本机。

用户确认 COS 桶还供其他项目使用，因此保留桶 ACL。只为本项目写入的对象显式设置 private，包括浏览器上传。桶 CORS 新增本项目来源 `https://work.dazhao.fun`，允许 PUT/GET/HEAD，允许 Content-Type 与 x-cos-acl，暴露 ETag。

## 构建网络

Docker Hub 官方入口在服务器连接超时，使用腾讯云镜像地址。Python 依赖从 uv.lock 导出固定版本及哈希，再通过腾讯云 PyPI 镜像安装；Debian 依赖使用腾讯云镜像。构建配置通过参数传入，仓库 Dockerfile 默认仍使用官方源。

首次 Chromium 下载使用同版本官方 Linux 归档，经本机下载后通过 SSH 传入服务器，在 Docker 网桥提供临时下载源。构建完成后下载服务及归档已删除，镜像内保留 Chromium 145.0.7632.6；渲染器使用 `channel="chromium"`，不依赖另行下载的 headless shell。后续构建可以复用缓存；首次部署到新机器或浏览器版本变化时需要能够访问官方 Playwright 下载地址。

## 日常命令

在服务器执行：

```bash
cd /opt/pixelle-studio
sudo docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build app
sudo docker compose -f docker-compose.yml -f docker-compose.production.yml ps
sudo docker compose -f docker-compose.yml -f docker-compose.production.yml logs --tail 100 app
curl -fsS http://127.0.0.1:8000/health
sudo nginx -t
```

修改 `.env` 后使用 `up -d app` 更新容器配置。不要执行 `down -v`，该选项会删除持久数据库卷。

## 验收状态

2026-11-05：应用正式运行，入口 `https://work.dazhao.fun`。用户放行 TCP 443 后公网证书校验通过，80 跳转 HTTPS，健康接口 200。宿主机 NGINX 与 Docker 服务已启用开机启动，应用容器 restart unless-stopped，状态 healthy；应用端口仅监听宿主机 127.0.0.1:8000。

- 历史导入 83 条，失败 0 条；引用素材约 5.63 GB，源文件保留。66 条已完成、14 条失败、3 条旧运行记录启动后标记为中断。原作品可播放、下载和查看分镜。
- COS 注册 31 个视频模板、1 首背景音乐。新业务对象私有，桶 ACL 保持原状，真实签名 GET/PUT、匿名 403、CORS 预检和浏览器直传确认均通过。
- HTTPS 登录 200，匿名业务接口 401，Secure/HttpOnly/SameSite Cookie 正常。SQLite backup API 制作迁移快照后安装到服务器，integrity_check 为 ok；容器重建后历史和设置仍在。
- 历史视频和新生成视频均通过 307 签名跳转与 Range 206 分段读取。真实 1080×1920 模板预览、MiniMax 音色列表与配音通过。
- 使用固定文案、静态模板和已配置 MiniMax 完成真实视频创作：后台任务、COS 下载、音频生成、Chromium 渲染、FFmpeg 合成、COS 发布及播放均通过；验收作品和上传测试对象已删除，最终仍为原 83 条历史。
- 公网 Chromium 桌面 1440px 和手机 390px 登录、创作、作品库、详情页检查通过，无横向溢出或脚本异常。Chromium/WebKit 自动化 14 项通过；Python 回归 39 项及余额不足专项 1 项通过，前端编译构建和差异检查通过。
- 修复首页目录被路径校验拒绝的问题，并验证首页、作品库及详情的直接访问。新草稿读取已有配音设置，已有草稿的用户选择继续保留。
- 2026-11-05 恢复新草稿的画面提示词默认值：读取 SQLite 设置中的 `media.image.prompt_prefix`，保留原火柴人风格。入口为「更多创作选项 → 画面风格描述（Prompt 前缀）」，提供「使用系统默认风格」按钮；已有草稿的自定义值或主动清空继续保留。固定模板效果图用于展示排版，AI 风格前缀影响实际画面生成。
- 2026-11-05 工作台阅读体验调整：提高文字对比和字号，精简顶部宣传及辅助文案，步骤明确为「创作内容、画面与风格、配音与音乐」。限制桌面内容宽度，缩小空输入区，预览随画幅自适应；桌面及手机生成操作栏保持可见。内置模板仍使用本地静态效果图。
- 2026-11-05 画面设置区精简：删除重复的缩略图、风格名称/规格及「真实效果图」标签，将「更换风格」移到标题右侧，下方直接显示模型选择。风格信息集中在大预览；手机提供「查看预览」入口切换到大图。已清理对应缩略图样式和组件参数。
- 2026-11-05 下拉框统一外观：修复配音服务紧凑 select 的原生箭头/边框叠加，补足右侧间距，全站 select 使用一致的单箭头；保留原生选项交互，强制颜色模式沿用系统箭头。

## 已确认的外部限制

2026-11-05 投资页面发布（`investment-20261105-224544`）：新增 `/investment`、鉴权投资接口、独立 60 秒采集、Excel/CSV 导入与审计记录，以及总资产、累计盈亏和跟踪以来 TWR 三张曲线。收益率以 2026 年 10 月 20 日日终资产市值为 0% 基准；数量显示整数，现价与单位成本三位小数，红涨绿跌，详细口径集中在“数据说明”。

投资数据独立传输并以事务合并投资表，导入 20 条规范记录、10 项持仓；合并前后逐表核对原平台数据一致，服务器 84 条作品保持完整，数据库 integrity_check 为 ok。部署前备份 `/data/platform-before-investment-20261105-224544.sqlite3`，保留镜像 `pixelle-studio-app:before-investment-20261105-224544`；发布镜像为 `pixelle-studio-app:investment-20261105-224544`。个人账本不进入源码或镜像。

验证：149 项 Python 平台回归、48 项 Chromium/WebKit 浏览器回归及前端构建通过。公网 HTTPS 登录、匿名接口 401、10 项持仓、20 条记录、TWR 起点为 0%，以及两种浏览器的 1440px/390px 投资页、说明弹窗、创作与作品库均通过；容器 healthy。生产后台连续采样时间为 14:47:59 和 14:48:57 UTC，刷新错误为空。

2026-11-05 模板存储调整：按用户最新要求，31 个内置模板直接从应用目录读取，31 张真实效果图约 390 KB，作为 Web 静态资源随镜像发布。风格选择列表、已选小图和默认大预览均直接加载真实效果图；没有固定模板渲染请求或定时刷新。个人文案或模板参数修改才生成个人预览，继续存 COS。原 COS 的内置模板副本不再参与当前运行，历史模板 ID 保持一致。

2026-11-05 后续界面更新：风格改为自动真实预览，取消手动“预览效果”。450ms 防抖后自动渲染当前模板、标题、固定文案及模板参数；更新保留上一张画面，失败可重试。手机风格区显示缩略图并支持放大，桌面右侧实时更新。18 项浏览器测试、33 项平台测试及公网桌面/手机真实 COS 图片验收通过；应用容器 healthy，预览临时目录清空。

当前 DeepSeek 账号在线文案请求返回 402 Insufficient Balance。主题生成文案需要充值，或在创作设置中切换可用 LLM；界面会显示余额不足提示。固定文案创作不受此限制，完整流程已验证。未对全部付费图片/视频模型逐一发起生成请求。

Edge 免费配音从本服务器请求被上游拒绝（403），界面提示选择 MiniMax 或稍后重试。已配置 MiniMax 可正常生成；系统沿用用户现有 API 配音默认值，不自动替换模型凭据或扣费服务选择。

证书于 2027-02-02 到期，需要提前替换证书并 reload NGINX。作品媒体存 COS；SQLite 设置及索引仍保存在持久卷，备份时用 SQLite backup API 并保护其中模型凭据。当前未配置定时数据库备份。

## 微信访问恢复与安全加固（2026-11-05）

微信恢复访问流程要求网站根路径提供 TXT 所有权验证文件。服务器通过 NGINX 的精确 location 从 `/var/www/work.dazhao.fun-verification` 提供，文件不需要登录；应用发布不影响验证文件。当前 HTTPS 访问返回 200、text/plain，内容精确匹配微信给出的 40 个字符。验证令牌仅留在服务器，不加入源码。

安全版本 `security-20261105-230719` 已上线。网页新增 CSP（脚本仅同源、禁止嵌入框架与插件）、X-Frame-Options 与 HSTS；上传确认增加图片格式与解码验证、限制图片像素，音频按指定格式以限时 ffprobe 检查。确认前下载限制大小，确认后的文件另存到客户端 PUT 链接无权覆盖的位置。模板参数按声明类型校验，渲染时统一转义非字符串值，拒绝颜色和数字参数中的 CSS 注入。

167 项平台测试与前端构建通过，Chromium/WebKit 的线上桌面与手机投资页、登录、创作入口和作品库回归通过；保留 84 个作品及 20 笔投资记录。服务器发布目录包含先前源码、镜像标签 `pixelle-studio-app:before-security-20261105-230719`，数据库备份 `/data/platform-before-security-20261105-230719.sqlite3` 已检查完整性。微信是否恢复访问仍以其申请审核结果为准，现有证据不能确认拦截原因为上述防护缺口。
