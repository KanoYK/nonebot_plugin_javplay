# Troubleshooting

## 更新jav 新增 0 个

检查 FlareSolverr：

```bash
curl http://YOUR_FLARESOLVERR_HOST:8191/
```

再看插件日志：

```text
log/javplay_YYYY-MM-DD.log
```

如果页面标题是 404、超时或验证页，说明 FlareSolverr 所在网络访问 JavDB 有问题。

## Aria2 下载完成但 Jellyfin 没有影片

检查三个路径是否指向同一个目录：

```text
javplay_db_path
javplay_jellyfin_media_path
javplay_aria2_dir
```

Aria2 任务路径必须是：

```text
javplay_aria2_dir/番号/番号.mp4
```

## 没有 Jellyfin 弹窗

插件只通知触发点播的用户。检查：

- Webhook 是否带 `UserId`。
- Jellyfin 当前是否有该用户活跃 Session。
- 客户端是否支持消息弹窗。
- `javplay_jellyfin_api_key` 是否有效。

## 115 任务已存在

正常。插件会继续在 115 已有文件里搜索正片。

## Aria2 tellStatus 400

通常是 Aria2 容器重启导致 GID 丢失。插件会检查本地文件兜底，但仍建议配置：

```ini
save-session=/config/aria2.session
input-file=/config/aria2.session
save-session-interval=60
force-save=true
```

## 旧番号被误触发

插件会校验 `wait.mp4?video_id=番号` 与 Jellyfin 活跃播放会话是否一致。若没有匹配，会只播放等待视频，不会排队下载。

若仍误触发，检查 Jellyfin 客户端是否在恢复播放旧项目，或 webhook payload 中的 ItemName 是否不是当前项目。

