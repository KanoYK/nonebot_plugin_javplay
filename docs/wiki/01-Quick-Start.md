# Quick Start

## 1. 前置条件

你需要先准备：

- 一个能正常收发 QQ 消息的 NapCat + NoneBot2。
- 一个能正常播放本地媒体的 Jellyfin。
- 一个可用的 Jellyfin MetaTube 插件和 MetaTube Server。
- 一个 Aria2 RPC 服务。
- 一个 FlareSolverr 服务。
- 一个可用于你本人合法内容的 115 账号。

请先确认你准备管理的内容允许被你访问、保存、缓存和播放；本项目不提供任何媒体内容，也不替代你对所在地法律法规和第三方平台规则的判断。

## 2. 安装插件

把插件目录复制到 NoneBot 项目的 `plugins/` 下：

```text
plugins/nonebot_plugin_javplay_public
```

安装依赖：

```bash
uv pip install -r plugins/nonebot_plugin_javplay_public/requirements.txt
```

在 `pyproject.toml` 中加载插件：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_javplay_public"]
plugin_dirs = ["plugins"]
```

## 3. 写入 `.env`

从 `.env.example` 复制配置项到 NoneBot 项目根目录的 `.env`，至少填写：

- `javplay_flaresolverr_url`
- `javplay_aria2_rpc`
- `javplay_aria2_secret`
- `javplay_aria2_dir`
- `javplay_jellyfin_url`
- `javplay_jellyfin_api_key`
- `javplay_db_path`
- `javplay_cache_host_path`
- `javplay_jellyfin_media_path`
- `javplay_strm_url`
- `javplay_115_savepath`

## 4. 配置 Jellyfin Webhook

Webhook URL：

```text
http://YOUR_NONEBOT_HOST:14514/webhook/jellyfin
```

如果设置了 `javplay_webhook_token`：

```text
http://YOUR_NONEBOT_HOST:14514/webhook/jellyfin?token=YOUR_WEBHOOK_TOKEN
```

## 5. 测试

重启 NoneBot 后，在 QQ 群中由管理员发送：

```text
更新jav
```

如果成功，会返回新增虚拟影片数量、跳过数量和刷新结果。

首次进入缓存流程时，插件可能要求 115 扫码。扫码后 Cookie 会保存在插件目录，下次自动复用。


