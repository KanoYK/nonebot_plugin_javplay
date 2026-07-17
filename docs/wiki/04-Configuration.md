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
javplay_crawl_pages_daily=5
javplay_daily_crawl_hour=5
javplay_daily_crawl_minute=0
javplay_scheduler_timezone="Asia/Shanghai"
javplay_full_scan_pages_per_run=50
```

推荐增加：

```toml
javplay_webhook_token="A_LONG_RANDOM_TOKEN"
javplay_cleanup_enabled=true
javplay_cleanup_keep_hours=24
javplay_cleanup_interval_minutes=60
```

如果 FlareSolverr 访问目标索引站点需要代理：

```toml
javplay_flaresolverr_proxy="http://YOUR_PROXY_HOST:7890"
```

完整配置见 `.env.example`。

## 触发范围

`javplay_jellyfin_media_path` 不只是路径映射，也用于限制触发范围。Jellyfin webhook 和等待视频推断只会处理路径位于该目录下的播放项，其他媒体库不会触发 JavPlay。

## 扫描任务

- `更新jav`：测试用，只爬最新少量页面。
- `完全扫描jav`：从 `page.json` 记录的页码开始，持续扫描直到数据库末尾。
- 每日任务：默认北京时间 05:00 爬取最新 `javplay_crawl_pages_daily` 页。

`javplay_full_scan_pages_per_run=50` 表示全量扫描的内部批次大小，不表示只扫描 50 页。


