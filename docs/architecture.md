# 创作平台架构

前端采用 React / TypeScript / Vite，统一登录、导航、表单、弹窗、状态和移动端布局。当前路由为快速创作、作品库、作品详情；设置以面板打开。

API 装配与鉴权在 `api/app.py`。`api/routes/shared.py` 提供设置、模型、资源和上传；`previews.py` 提供视频预览；`video.py` 提供视频生成；`works.py` 提供作品列表、素材访问和视频编辑。请求契约集中在 `api/schemas/studio.py`，页面不传服务器文件路径。

`pixelle_video/platform` 提供公共 SQLite、COS、资源、队列、登录与运行时。SQLite 持久化作品索引、任务、批次标识、设置、会话；COS 保留正文/分镜、素材、检查点和不可变修订清单。本地只存在用于 FFmpeg/Chromium 的临时工作区。

视频模块位于 `pixelle_video/pipelines/standard.py` 和 `services`，只调用直连服务商 API 或 Edge TTS。`PersistenceService` 将分镜字段转换为 COS 引用，处理时下载到独立临时目录；上传成功再切换 SQLite 清单指针，旧已发布修订不会被失败编辑覆盖。

## 增加文章创作

内置模板与固定效果图是应用静态资源：源文件在 `templates/`，效果图在 `frontend/public/template-previews/`，随镜像和 Web 发布，不存 COS。注册资源层按原稳定 ID 读取内置模板，历史作品无需修改模板引用。只有用户内容、素材、自定义资源和个人预览使用 COS。模板内容改变时预先重建效果图，浏览器刷新读取新文件；浏览固定风格不会发起渲染。

LLM/VLM 客户端、模型预设、连接检查工具和通用提示词作为共享扩展能力保留，位于 `services/api_services/llm_*.py`、`services/api_services/vlm_*.py`、`llm_presets.py`、`utils/llm_util.py` 及 `prompts`。当前视频创作使用 `LLMService`；新增文章或图文理解模块可按需要复用这些客户端。精简页面和退役工作流时，不因某个共享模块暂未被当前页面调用而删除它。

新建文章页面与路由，例如 `/articles/new`，使用同一套 UI 控件与登录。新增 `api/routes/articles.py`，注册到已鉴权 API Router。文章业务服务独立实现，不继承视频流水线，不依赖 FFmpeg 或分镜结构。

通过 `platform.jobs.register('article.generate', handler)` 注册后台任务。handler 使用共享 LLM 设置，文章正文和附件写入 COS，目录记录 `kind='article'`，文章阅读/编辑由文章模块处理。统一作品库通过 kind 搜索与筛选，文章可有自己的详情页面；视频专用编辑始终检查作品类型。新增模块无需修改公共登录、COS 适配器、资源上传或队列核心。

当前队列单进程串行；SQLite 使用 WAL、线程锁和同作品互斥。将来需要多实例时迁移到 PostgreSQL 与外部队列，实现事务领取、租约和分布式互斥；对象存储引用及前端请求模型保持不变。

## 边界与可靠性

浏览器只取得登记资源的短期签名 URL。登录账号和密码从服务器环境读取，启动时在内存中生成密码校验值，不写入数据库或返回浏览器；服务器密钥同样不返回浏览器。COS 清单过滤密钥、回调和处理输出路径。对状态变更请求校验来源，同域 session Cookie。未配置账号/COS 时启动失败，防止把演示替身用于生产。

每项任务隔离处理目录和浏览器上下文，生成与编辑通过临时文件交给 FFmpeg，任务退出即清理。检查点可在清空临时目录后恢复。取消为协作式，重启任务标记中断，不自动再次调用模型。

历史迁移读取原始文件、完整上传后入库，可重复执行，不删除源文件；已删除的工作流名称只用于旧记录兼容，不能执行。
