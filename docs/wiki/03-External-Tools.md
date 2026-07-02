# External Tools

## MetaTube Server

MetaTube Server 是 Jellyfin MetaTube 插件的后端。推荐数据库模式：

```bash
./metatube-server -dsn metatube.db -port 8080
```

Docker：

```bash
docker run -d \
  --name metatube \
  -p 8080:8080 \
  -v $PWD/config:/config \
  ghcr.io/metatube-community/metatube-server:latest \
  -dsn /config/metatube.db
```

Jellyfin MetaTube 插件中填写：

```text
http://YOUR_METATUBE_HOST:8080
```

## FlareSolverr

Docker Compose：

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

`.env`：

```toml
javplay_flaresolverr_url="http://YOUR_FLARESOLVERR_HOST:8191"
```

## Aria2

`aria2.conf` 关键项：

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
```

`.env`：

```toml
javplay_aria2_rpc="http://YOUR_ARIA2_HOST:6800/jsonrpc"
javplay_aria2_secret="YOUR_ARIA2_RPC_SECRET"
javplay_aria2_dir="/PATH_IN_ARIA2_CONTAINER/JAV"
```

## 115

无需提前填写 Cookie。首次触发下载时，插件会输出二维码并保存 `QCcode.jpg`。扫码后保存 Cookie。

如果 Cookie 失效，删除：

```text
115_cookie.txt
115_download_cookie.txt
```

然后重新触发下载并扫码。


