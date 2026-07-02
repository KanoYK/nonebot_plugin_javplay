# Path Mapping

JavPlay 最容易出错的是路径映射。

核心原则：NoneBot、Jellyfin、Aria2 看到的路径可以不同，但必须挂载到同一个真实目录。

| 角色 | 配置项 | 示例 |
| --- | --- | --- |
| NoneBot 宿主机 | `javplay_db_path` | `/PATH_ON_BOT_HOST/JAV` |
| NoneBot 宿主机 | `javplay_cache_host_path` | `/PATH_ON_BOT_HOST/JAV` |
| Jellyfin 容器 | `javplay_jellyfin_media_path` | `/PATH_IN_JELLYFIN_CONTAINER/JAV` |
| Aria2 容器 | `javplay_aria2_dir` | `/PATH_IN_ARIA2_CONTAINER/JAV` |

同一个真实文件应该表现为：

```text
/PATH_ON_BOT_HOST/JAV/ABC-123/ABC-123.mp4
/PATH_IN_JELLYFIN_CONTAINER/JAV/ABC-123/ABC-123.mp4
/PATH_IN_ARIA2_CONTAINER/JAV/ABC-123/ABC-123.mp4
```

## 检查方法

1. 在 Aria2 中看任务保存路径，应是 `/PATH_IN_ARIA2_CONTAINER/JAV/番号/番号.mp4`。
2. 在 NoneBot 服务器执行 `ls /PATH_ON_BOT_HOST/JAV/番号`，应看到同一个文件。
3. 进入 Jellyfin 容器执行 `ls /PATH_IN_JELLYFIN_CONTAINER/JAV/番号`，应看到同一个文件。

如果任意一边看不到，说明 Docker volume 或 NAS 挂载没有对齐。


