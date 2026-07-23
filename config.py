from typing import Optional
from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Optional QQ notification target.
    javplay_qq_user: int = 0
    javplay_qq_group: int = 0

    # Storage mode. "aria2_cache" keeps the old 115 -> Aria2 -> local cache flow.
    # "115_mount"/"clouddrive_mount" stores selected videos in 115 and waits for Jellyfin
    # to see the mounted path.
    javplay_storage_mode: str = "aria2_cache"

    # 115. Leave the cookie empty to use QR-code login on first download.
    javplay_115_cookie: str = ""
    javplay_115_savepath: str = ""
    javplay_115_mount_jellyfin_path: str = ""
    javplay_115_min_video_size_mb: int = 300
    javplay_115_junk_keywords: str = (
        "广告,直播,最新地址,最新位址,社區,社区,收藏不迷路,防迷路,"
        "网址,地址,防走丢,489155.com,.html,.txt"
    )
    javplay_115_require_wanted_selection: bool = True
    
    # JavDB proxy used by direct HTTP requests. Leave empty when unused.
    javplay_proxy_http: Optional[str] = None
    
    # FlareSolverr endpoint, without the /v1 suffix.
    javplay_flaresolverr_url: Optional[str] = None
    javplay_flaresolverr_proxy: Optional[str] = None
    
    # Aria2
    javplay_aria2_rpc: str = ""
    javplay_aria2_secret: str = ""
    javplay_aria2_dir: str = ""
    javplay_aria2_poll_interval_seconds: int = 30
    javplay_aria2_poll_timeout_hours: int = 24
    javplay_local_complete_min_size_mb: int = 50
    
    # Jellyfin API
    javplay_jellyfin_url: str = ""
    javplay_jellyfin_api_key: str = ""
    javplay_webhook_token: str = ""
    javplay_jellyfin_message_timeout_ms: int = 30000
    javplay_jellyfin_item_refresh_wait_seconds: int = 180
    javplay_jellyfin_item_refresh_interval_seconds: int = 5
    
    # Library Builder
    javplay_db_path: str = ""  # Bot-visible path for phantom entries and cached files.
    javplay_cache_host_path: Optional[str] = None
    javplay_jellyfin_media_path: str = ""  # Jellyfin container path for the same media root.
    javplay_crawl_pages_daily: int = 5
    javplay_daily_crawl_hour: int = 5
    javplay_daily_crawl_minute: int = 0
    javplay_scheduler_timezone: str = "Asia/Shanghai"
    javplay_crawl_start_page: int = 1
    javplay_crawl_max_page: int = 2000
    javplay_full_scan_state_file: str = "page.json"
    javplay_full_scan_pages_per_run: int = 50  # Internal batch size; full scan continues until done.
    javplay_strm_url: str = ""
    javplay_manual_crawl_pages: int = 1
    javplay_manual_crawl_max_pages: int = 3

    # Local download cleanup
    javplay_cleanup_enabled: bool = True
    javplay_cleanup_keep_hours: int = 24
    javplay_cleanup_interval_minutes: int = 60

