# JavPlay

JavPlay 是一个 NoneBot2 插件，用于把 Jellyfin 做成“虚拟影片入口 + 本地缓存编排”的流程。它只适合管理你已经取得合法授权、且允许在所在地保存和访问的个人媒体内容：

1. 从公开索引页面读取条目信息。
2. 在 Jellyfin 媒体库目录中创建 `.strm` 虚拟影片。
3. Jellyfin 通过 MetaTube 刮削元数据。
4. 用户播放虚拟影片时，插件显示等待视频并开始后台缓存流程。
5. 插件把用户配置的资源标识提交给 115 任务能力。
6. 插件从 115 获取可用文件地址并推送到 Aria2。
7. Aria2 将缓存文件保存到 Jellyfin 媒体目录。
8. 插件刷新单个 Jellyfin 媒体项，确认缓存文件出现后删除虚拟 `.strm`。
9. 插件只向触发点播的 Jellyfin 用户发送完成通知。

本目录是可公开发布版本：没有 115 Cookie、Jellyfin API Key、账号密码、私人 IP、个人路径等默认值。所有需要因人而异的设置都从 NoneBot 项目的 `.env` 读取。

> 合规提示：本项目不提供、托管、分发或授权任何媒体内容，也不保证第三方索引、网盘、下载工具或元数据服务的使用在你的司法辖区内合法。请只处理你有权访问、保存、转码、缓存和播放的内容；不要将本项目用于规避访问控制、批量抓取、传播侵权内容或违反任何平台服务条款的用途。

## 适合谁

这个 README 假设你已经能完成基础的：

- 安装 Jellyfin。
- 安装 NapCat + NoneBot2。
- 能把一个插件目录放进 NoneBot 的 `plugins/` 目录。
- 会编辑 `.env`。

如果你还没有部署过 NoneBot2 或 OneBot V11，请先把 NapCat 和 NoneBot 的基本收发消息跑通，再接入本插件。

## 核心概念

### 三个路径必须指向同一个媒体目录

JavPlay 同时被 NoneBot、Jellyfin、Aria2 使用。它们看到的路径可能不同，但底层必须是同一个目录。

| 使用者 | 配置项 | 示例 | 说明 |
| --- | --- | --- | --- |
| NoneBot 插件所在机器 | `javplay_db_path` | `/PATH_ON_BOT_HOST/JAV` | 插件创建 `.strm`、检查缓存文件、清理缓存 |
| NoneBot 插件所在机器 | `javplay_cache_host_path` | `/PATH_ON_BOT_HOST/JAV` | 插件寻找真实缓存文件；通常与 `javplay_db_path` 相同 |
| Jellyfin 容器 | `javplay_jellyfin_media_path` | `/PATH_IN_JELLYFIN_CONTAINER/JAV` | Jellyfin 媒体库中添加的路径 |
| Aria2 容器 | `javplay_aria2_dir` | `/PATH_IN_ARIA2_CONTAINER/JAV` | Aria2 保存缓存文件的路径 |
| 115 网盘 | `javplay_115_savepath` | `/PATH_IN_115_CLOUD/JAV` | 115 任务保存路径 |

例如同一部影片应当能在三边看到：

```text
NoneBot 看到:  /PATH_ON_BOT_HOST/JAV/ABC-123/ABC-123.mp4
Jellyfin 看到: /PATH_IN_JELLYFIN_CONTAINER/JAV/ABC-123/ABC-123.mp4
Aria2 看到:   /PATH_IN_ARIA2_CONTAINER/JAV/ABC-123/ABC-123.mp4
```

如果这三个路径没有映射到同一个目录，常见现象是：Aria2 显示任务完成，但 Jellyfin 看不到影片；或插件一直等待缓存文件出现。

### 虚拟影片和等待视频

插件会为每个番号创建 `.strm` 文件，内容类似：

```text
http://YOUR_NONEBOT_HOST:14514/wait.mp4?video_id=ABC-123
```

Jellyfin 播放这个地址时，会看到插件目录中的 `wait.mp4`。插件会结合 Jellyfin 当前活跃播放会话确认用户真的在播放该番号，确认后才会加入缓存队列，避免 Jellyfin 客户端预加载旧视频导致误触发。

插件还会校验当前 Jellyfin 播放项的文件路径必须位于 `javplay_jellyfin_media_path` 下。其他媒体库即使标题里包含类似番号的文本，也不会触发 JavPlay 下载流程。

## 文件结构

```text
nonebot_plugin_javplay_public/
├── __init__.py              # 插件入口、路由、命令、调度、缓存编排
├── config.py                # NoneBot .env 配置模型
├── scraper.py               # 公开索引读取与资源标识解析
├── library_builder.py       # 虚拟库创建、全量扫描、清理恢复
├── downloader_115.py        # 115 登录、任务提交、文件地址获取
├── downloader.py            # Aria2 RPC
├── jellyfin_api.py          # Jellyfin API、通知、单项目刷新
├── aria2_complete.py        # 可选 Aria2 完成回调脚本
├── wait.mp4                 # 等待视频
├── requirements.txt         # Python 依赖
├── .env.example             # 配置示例
├── docs/wiki/               # GitHub Wiki 页面源文件
└── README.md
```

## 安装插件

### 1. 放入 NoneBot 插件目录

把整个目录复制到 NoneBot 项目的 `plugins/` 目录，例如：

```text
/home/nonebot/mybot/plugins/nonebot_plugin_javplay_public
```

如果你希望插件名仍然叫 `nonebot_plugin_javplay`，可以把目录重命名为：

```text
/home/nonebot/mybot/plugins/nonebot_plugin_javplay
```

两种方式都可以，关键是 `pyproject.toml` 或插件加载配置里的名称要和目录名一致。

### 2. 安装插件依赖

在 NoneBot 项目根目录执行：

```bash
uv pip install -r plugins/nonebot_plugin_javplay_public/requirements.txt
```

如果你不用 uv，也可以：

```bash
pip install -r plugins/nonebot_plugin_javplay_public/requirements.txt
```

依赖包括：

| 依赖 | 用途 |
| --- | --- |
| `nonebot2` | NoneBot 插件运行 |
| `nonebot-plugin-apscheduler` | 每日索引同步和本地清理定时任务 |
| `beautifulsoup4` | 解析公开索引页面 |
| `httpx` | 异步 HTTP 请求 |
| `requests` | 115 ProAPI 和回调脚本 |
| `p115client` | 115 登录、任务处理、网盘文件查询 |
| `qrcode[pil]` | 生成 115 登录二维码图片 |

### 3. 加载插件

如果你的 NoneBot 使用 `pyproject.toml` 管理插件，示例：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_javplay_public"]
plugin_dirs = ["plugins"]
```

如果你把目录重命名成 `nonebot_plugin_javplay`，则写：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_javplay"]
plugin_dirs = ["plugins"]
```

### 4. 确认等待视频存在

插件目录内应当有：

```text
wait.mp4
```

Jellyfin 能访问 `javplay_strm_url` 后，播放虚拟影片时就会显示这个等待视频。

## `.env` 配置

NoneBot 会读取 `.env` 和 `.env.{ENVIRONMENT}`。建议把 JavPlay 配置放在 NoneBot 项目根目录的 `.env` 或 `.env.prod` 中。

先复制插件里的 `.env.example`，然后按你的机器改值。下面是完整说明。

### 最小必填配置

```toml
# 115 网盘任务保存目录
javplay_115_savepath="/PATH_IN_115_CLOUD/JAV"

# FlareSolverr 地址
javplay_flaresolverr_url="http://YOUR_FLARESOLVERR_HOST:8191"

# Aria2
javplay_aria2_rpc="http://YOUR_ARIA2_HOST:6800/jsonrpc"
javplay_aria2_secret="YOUR_ARIA2_RPC_SECRET"
javplay_aria2_dir="/PATH_IN_ARIA2_CONTAINER/JAV"

# Jellyfin
javplay_jellyfin_url="http://YOUR_JELLYFIN_HOST:8096"
javplay_jellyfin_api_key="YOUR_JELLYFIN_API_KEY"

# 路径映射
javplay_db_path="/PATH_ON_BOT_HOST/JAV"
javplay_cache_host_path="/PATH_ON_BOT_HOST/JAV"
javplay_jellyfin_media_path="/PATH_IN_JELLYFIN_CONTAINER/JAV"

# Jellyfin 能访问到的 NoneBot 等待视频地址
javplay_strm_url="http://YOUR_NONEBOT_HOST:14514/wait.mp4"
```

### 完整配置

见本目录：

```text
.env.example
```

### 配置项解释

| 配置项 | 是否必填 | 说明 |
| --- | --- | --- |
| `javplay_115_cookie` | 否 | 115 Cookie。留空时插件会使用二维码登录 |
| `javplay_115_savepath` | 是 | 115 任务保存目录 |
| `javplay_proxy_http` | 否 | 直接请求公开索引页面时使用的 HTTP 代理 |
| `javplay_flaresolverr_url` | 是 | FlareSolverr 地址，不要带 `/v1` |
| `javplay_flaresolverr_proxy` | 否 | 传给 FlareSolverr 的上游代理 |
| `javplay_aria2_rpc` | 是 | Aria2 JSON-RPC 地址 |
| `javplay_aria2_secret` | 是 | Aria2 RPC Secret |
| `javplay_aria2_dir` | 是 | Aria2 容器内缓存目录 |
| `javplay_jellyfin_url` | 是 | Jellyfin 服务地址 |
| `javplay_jellyfin_api_key` | 是 | Jellyfin API Key |
| `javplay_webhook_token` | 建议 | Webhook 共享密钥，内网也建议设置 |
| `javplay_db_path` | 是 | NoneBot 机器看到的媒体目录 |
| `javplay_cache_host_path` | 是 | NoneBot 机器检查缓存文件的目录 |
| `javplay_jellyfin_media_path` | 是 | Jellyfin 容器内媒体库路径 |
| `javplay_strm_url` | 是 | 写入 `.strm` 的等待视频 URL |
| `javplay_crawl_pages_daily` | 否 | 每日自动同步最新索引页数 |
| `javplay_daily_crawl_hour` | 否 | 每日更新小时，默认北京时间 5 点 |
| `javplay_daily_crawl_minute` | 否 | 每日更新分钟 |
| `javplay_scheduler_timezone` | 否 | 定时任务时区，默认 `Asia/Shanghai` |
| `javplay_full_scan_pages_per_run` | 否 | `完全扫描jav` 的内部批次大小；命令会连续跑完，不再只跑这一批 |
| `javplay_cleanup_enabled` | 否 | 是否启用本地缓存清理 |
| `javplay_cleanup_keep_hours` | 否 | 本地真实文件保留小时数 |

## Jellyfin 配置

### 1. 安装 MetaTube 插件

在 Jellyfin 中安装 MetaTube 插件，并将你的 JavPlay 媒体库设置为由 MetaTube 刮削。

MetaTube 需要后端服务 `metatube-server`。官方文档提供直接运行、Docker 和 Docker Compose 部署方式。推荐使用数据库模式持久化：

```bash
./metatube-server -dsn metatube.db -port 8080
```

Docker 示例：

```bash
docker run -d \
  --name metatube \
  -p 8080:8080 \
  -v $PWD/config:/config \
  ghcr.io/metatube-community/metatube-server:latest \
  -dsn /config/metatube.db
```

Jellyfin 的 MetaTube 插件中，把后端地址填为：

```text
http://YOUR_METATUBE_HOST:8080
```

参考：<https://metatube-community.github.io/wiki/server-deployment/>

### 2. 添加媒体库

在 Jellyfin 中添加媒体库目录，也就是 Jellyfin 容器看到的路径：

```text
/PATH_IN_JELLYFIN_CONTAINER/JAV
```

这个路径要与 `.env` 中的 `javplay_jellyfin_media_path` 一致。

### 3. 创建 Jellyfin API Key

进入 Jellyfin 管理后台：

```text
控制台 -> 高级 -> API 密钥 -> 新建
```

复制生成的 Key，填入：

```toml
javplay_jellyfin_api_key="YOUR_JELLYFIN_API_KEY"
```

不要把真实 Key 写进 README、GitHub Issue 或公开日志。

### 4. 配置 Jellyfin Webhook

安装 Jellyfin Webhook 插件后添加目标：

```text
http://YOUR_NONEBOT_HOST:14514/webhook/jellyfin
```

如果设置了 `javplay_webhook_token`：

```text
http://YOUR_NONEBOT_HOST:14514/webhook/jellyfin?token=YOUR_WEBHOOK_TOKEN
```

事件选择播放开始事件。如果不确定，也可以先选择全部事件，插件会自动忽略非 `PlaybackStart` / `PlaybackStarted` 事件。

可以用下面命令测试连通性：

```bash
curl -X POST http://YOUR_NONEBOT_HOST:14514/webhook/jellyfin \
  -H "Content-Type: application/json" \
  -d '{"NotificationType":"Ping"}'
```

返回类似下面内容是正常的：

```json
{"status":"ignored","reason":"Not PlaybackStart","event_type":"Ping"}
```

## FlareSolverr 配置

公开索引站点可能有 Cloudflare 验证，插件通过 FlareSolverr 获取页面。使用前请确认目标站点的访问规则、robots/服务条款以及你所在地的法律要求。

FlareSolverr 官方推荐 Docker，因为镜像内已经包含需要的浏览器环境。

Docker Compose 示例：

```yaml
services:
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    container_name: flaresolverr
    environment:
      - LOG_LEVEL=info
      - TZ=Asia/Shanghai
    ports:
      - "8191:8191"
    restart: unless-stopped
```

启动：

```bash
docker compose up -d
```

测试：

```bash
curl http://YOUR_FLARESOLVERR_HOST:8191/
```

然后在 `.env` 中写：

```toml
javplay_flaresolverr_url="http://YOUR_FLARESOLVERR_HOST:8191"
```

如果 FlareSolverr 所在机器访问目标索引站点也需要代理：

```toml
javplay_flaresolverr_proxy="http://YOUR_PROXY_HOST:7890"
```

参考：<https://github.com/FlareSolverr/FlareSolverr>

## Aria2 配置

插件通过 Aria2 JSON-RPC 处理 115 返回的可用文件地址。

### 基础 aria2.conf

下面是通用配置片段。不同 Docker 镜像的配置文件路径可能不同，但参数名基本一致。

```ini
enable-rpc=true
rpc-listen-all=true
rpc-listen-port=6800
rpc-secret=YOUR_ARIA2_RPC_SECRET

dir=/PATH_IN_ARIA2_CONTAINER/JAV
continue=true
allow-overwrite=true
auto-file-renaming=false

save-session=/config/aria2.session
input-file=/config/aria2.session
save-session-interval=60
force-save=true

max-connection-per-server=8
split=8
min-split-size=10M
```

`.env` 中对应：

```toml
javplay_aria2_rpc="http://YOUR_ARIA2_HOST:6800/jsonrpc"
javplay_aria2_secret="YOUR_ARIA2_RPC_SECRET"
javplay_aria2_dir="/PATH_IN_ARIA2_CONTAINER/JAV"
```

Aria2 的 RPC Secret 调用方式是把 `token:YOUR_SECRET` 放在 RPC 参数第一位，插件已经自动处理。

### 可选：Aria2 完成回调

插件本身会轮询 Aria2，并且在 Aria2 容器重启或 GID 丢失时检查本地文件兜底。因此完成回调不是必需的。

如果你仍希望使用回调，可以把 `aria2_complete.py` 放到 Aria2 容器能执行的位置，并设置：

```ini
on-download-complete=/config/aria2_complete.py
```

脚本读取环境变量：

```bash
JAVPLAY_ARIA2_WEBHOOK_URL="http://YOUR_NONEBOT_HOST:14514/webhook/aria2"
JAVPLAY_WEBHOOK_TOKEN="YOUR_WEBHOOK_TOKEN"
```

参考：<https://aria2.github.io/manual/en/html/aria2c.html>

## 115 登录

第一次需要 115 时，如果没有可用 Cookie，插件会：

1. 在 NoneBot 日志和插件日志中输出二维码文本。
2. 在插件目录保存 `QCcode.jpg`。
3. 扫码确认后保存 `115_cookie.txt`。
4. 获取文件地址时，可能额外保存 `115_download_cookie.txt`。

这些文件都被 `.gitignore` 忽略，不要上传。

如果 Cookie 过期，删除插件目录中的 `115_cookie.txt` 和 `115_download_cookie.txt`，下次触发缓存流程会重新扫码。

## 命令

以下命令需要 QQ 群管理员、群主或 NoneBot `SUPERUSER`：

```text
更新jav
```

测试同步最新少量索引页面。可用于确认索引访问、FlareSolverr、路径和 Jellyfin 刷新是否正常。

```text
完全扫描jav
```

持续扫描历史索引页面。命令会从 `page.json` 记录的页数开始，按 `javplay_full_scan_pages_per_run` 分批处理，但不会在一批后停止，而是持续扫描到页面为空或达到 `javplay_crawl_max_page`。请合理控制频率，并遵守目标站点规则。

也可以指定起始页重新扫描：

```text
完全扫描jav 120
```

表示从第 120 页开始持续扫描。

## 定时任务

安装 `nonebot-plugin-apscheduler` 后，插件会注册：

- 每天北京时间 05:00 同步最新 `javplay_crawl_pages_daily` 页索引。
- 每隔 `javplay_cleanup_interval_minutes` 分钟检查本地缓存清理。

每日任务只补最新内容；历史数据用 `完全扫描jav` 一次性持续扫到数据库末尾。

## 缓存清理逻辑

插件采用缓存模式：

- 初始状态：只有 `.strm` 虚拟影片。
- 用户点播：把已授权内容缓存到同一番号目录。
- 缓存完成：刷新 Jellyfin，确认真实媒体项出现后删除 `.strm`。
- 清理过期缓存：删除本地缓存文件，但不删除 115 网盘内容。
- 清理后：重新创建 `.strm`，保留 Jellyfin 点播入口。

清理任务不会清空整个媒体库目录，也不会删除 115 侧文件。

## 日志

插件会把自身日志写到：

```text
nonebot_plugin_javplay_public/log/javplay_YYYY-MM-DD.log
```

如果你把目录重命名为 `nonebot_plugin_javplay`，则日志在：

```text
nonebot_plugin_javplay/log/javplay_YYYY-MM-DD.log
```

日志保留 14 天，默认 DEBUG 级别。排障时优先看这个目录。

## 常见排障

### `更新jav` 新增 0 个影片

优先检查：

- FlareSolverr 是否能打开目标索引站点。
- `javplay_flaresolverr_url` 是否能被 NoneBot 访问。
- FlareSolverr 所在机器是否需要代理。
- 日志里的页面标题是否是 404、超时、验证页。

### Jellyfin 能播放等待视频，但没有开始缓存

插件会确认 Jellyfin 当前活跃播放会话中的番号与 `wait.mp4?video_id=番号` 一致。没有匹配会话时不会进入缓存队列。

检查：

- Jellyfin Webhook 是否配置。
- `javplay_jellyfin_url` 和 API Key 是否正确。
- Jellyfin `/Sessions` 中是否能看到当前播放。
- `.strm` 中的 `javplay_strm_url` 是否带了正确 `video_id`。

### 缓存完成但 Jellyfin 看不到真实影片

几乎都是路径映射问题。确认：

- Aria2 任务里的保存路径是 `javplay_aria2_dir/番号/番号.mp4`。
- NoneBot 机器上能在 `javplay_cache_host_path/番号/番号.mp4` 看到同一个文件。
- Jellyfin 容器内能在 `javplay_jellyfin_media_path/番号/番号.mp4` 看到同一个文件。

### 没有 Jellyfin 弹窗通知

插件只通知触发点播的 Jellyfin 用户，不广播给所有在线用户。检查：

- Webhook payload 是否包含 `UserId`。
- Jellyfin 当前是否有该用户的活跃 Session。
- 客户端是否支持 Jellyfin 消息弹窗。
- 插件日志中 `send_jellyfin_notification` 相关记录。

### 115 提示任务已存在

正常。插件会把 115 重复任务视为可继续状态，然后搜索 115 已有文件。

### Aria2 `tellStatus` 返回 400

常见于 Aria2 容器重启后 GID 丢失。插件会用本地文件兜底判断是否完成。为了减少这种情况，建议开启：

```ini
save-session=/config/aria2.session
input-file=/config/aria2.session
save-session-interval=60
force-save=true
```

## 参考文档

- NoneBot 配置：<https://nonebot.dev/docs/appendices/config>
- MetaTube Server：<https://metatube-community.github.io/wiki/server-deployment/>
- FlareSolverr：<https://github.com/FlareSolverr/FlareSolverr>
- Aria2 Manual：<https://aria2.github.io/manual/en/html/aria2c.html>


