# Configuration

配置写在 NoneBot 项目的 `.env` 或 `.env.prod`，不要写进插件代码。

最小配置：

```toml
javplay_115_savepath="/PATH_IN_115_CLOUD/JAV"
javplay_flaresolverr_url="http://YOUR_FLARESOLVERR_HOST:8191"
javplay_aria2_rpc="http://YOUR_ARIA2_HOST:6800/jsonrpc"
javplay_aria2_secret="YOUR_ARIA2_RPC_SECRET"
javplay_aria2_dir="/PATH_IN_ARIA2_CONTAINER/JAV"
javplay_jellyfin_url="http://YOUR_JELLYFIN_HOST:8096"
javplay_jellyfin_api_key="YOUR_JELLYFIN_API_KEY"
javplay_db_path="/PATH_ON_BOT_HOST/JAV"
javplay_cache_host_path="/PATH_ON_BOT_HOST/JAV"
javplay_jellyfin_media_path="/PATH_IN_JELLYFIN_CONTAINER/JAV"
javplay_strm_url="http://YOUR_NONEBOT_HOST:14514/wait.mp4"
```

推荐增加：

```toml
javplay_webhook_token="A_LONG_RANDOM_TOKEN"
javplay_cleanup_enabled=true
javplay_cleanup_keep_hours=24
javplay_cleanup_interval_minutes=60
```

如果 FlareSolverr 访问 JavDB 需要代理：

```toml
javplay_flaresolverr_proxy="http://YOUR_PROXY_HOST:7890"
```

完整配置见 `.env.example`。


