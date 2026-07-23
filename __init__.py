import asyncio
import json
import os
import re
import time
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, Request, Response
from nonebot import get_app, get_plugin_config, logger, on_command
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from pydantic import BaseModel

try:
    from nonebot import require

    require("nonebot_plugin_apscheduler")
    from nonebot_plugin_apscheduler import scheduler
except Exception:
    logger.warning("nonebot_plugin_apscheduler not loaded. Scheduled jobs won't run.")
    scheduler = None

try:
    from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER

    JAVPLAY_ADMIN_PERMISSION = SUPERUSER | GROUP_ADMIN | GROUP_OWNER
except Exception:
    logger.warning("OneBot v11 permissions not loaded. '更新jav' will be limited to superusers.")
    JAVPLAY_ADMIN_PERMISSION = SUPERUSER

from .config import Config
from .downloader import tell_status
from .downloader_115 import download_to_115_mount, download_via_115
from .jellyfin_api import (
    get_active_sessions,
    refresh_jellyfin_item,
    refresh_jellyfin_item_by_video_id,
    refresh_jellyfin_library,
    send_jellyfin_notification,
    wait_for_real_jellyfin_item,
)
from .library_builder import build_phantom_library, create_phantom_video, remove_phantom_video
from .scraper import search_magnet


plugin_config = get_plugin_config(Config)
app = get_app()
PLUGIN_DIR = os.path.dirname(__file__)
PLUGIN_LOG_DIR = os.path.join(PLUGIN_DIR, "log")


def _javplay_log_filter(record: dict) -> bool:
    record_name = record.get("name") or ""
    if record_name.startswith(("nonebot_plugin_javplay", "plugins.nonebot_plugin_javplay")):
        return True

    file_path = ""
    try:
        file_path = os.path.abspath(record["file"].path)
    except Exception:
        return False
    return file_path.startswith(os.path.abspath(PLUGIN_DIR) + os.sep)


try:
    os.makedirs(PLUGIN_LOG_DIR, exist_ok=True)
    logger.add(
        os.path.join(PLUGIN_LOG_DIR, "javplay_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation="00:00",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
        filter=_javplay_log_filter,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "{name}:{function}:{line} - {message}"
        ),
    )
    logger.info(f"JavPlay file logging enabled: {PLUGIN_LOG_DIR}")
except Exception as e:
    logger.warning(f"Failed to enable JavPlay file logging: {e}")


def _media_host_path(path: Optional[str] = None) -> str:
    host_root = plugin_config.javplay_cache_host_path or plugin_config.javplay_db_path
    source_path = path or plugin_config.javplay_db_path
    jellyfin_root = plugin_config.javplay_jellyfin_media_path

    if jellyfin_root and source_path == jellyfin_root:
        return host_root
    if jellyfin_root and source_path.startswith(jellyfin_root + "/"):
        return os.path.join(host_root, os.path.relpath(source_path, jellyfin_root))
    return source_path

logger.info(
    "JavPlay path config: "
    f"storage_mode={plugin_config.javplay_storage_mode}, "
    f"db_path={plugin_config.javplay_db_path}, "
    f"host_media_path={_media_host_path()}, "
    f"aria2_dir={plugin_config.javplay_aria2_dir}, "
    f"115_savepath={plugin_config.javplay_115_savepath}, "
    f"115_mount_jellyfin_path={plugin_config.javplay_115_mount_jellyfin_path or 'none'}, "
    f"flaresolverr_proxy={plugin_config.javplay_flaresolverr_proxy or 'none'}"
)

_BASE_REQUIRED_CONFIG_FIELDS = (
    "javplay_115_savepath",
    "javplay_jellyfin_url",
    "javplay_jellyfin_api_key",
    "javplay_db_path",
    "javplay_cache_host_path",
    "javplay_jellyfin_media_path",
    "javplay_strm_url",
)
_ARIA2_REQUIRED_CONFIG_FIELDS = (
    "javplay_aria2_rpc",
    "javplay_aria2_secret",
    "javplay_aria2_dir",
)
_115_MOUNT_REQUIRED_CONFIG_FIELDS = (
    "javplay_115_mount_jellyfin_path",
)


def _storage_mode() -> str:
    mode = (plugin_config.javplay_storage_mode or "aria2_cache").strip().lower()
    mount_modes = {"115", "115_mount", "mount", "cloud_mount", "clouddrive", "clouddrive_mount"}
    return "115_mount" if mode in mount_modes else "aria2_cache"


def _warn_missing_required_config() -> None:
    required_fields = list(_BASE_REQUIRED_CONFIG_FIELDS)
    if _storage_mode() == "115_mount":
        required_fields.extend(_115_MOUNT_REQUIRED_CONFIG_FIELDS)
    else:
        required_fields.extend(_ARIA2_REQUIRED_CONFIG_FIELDS)

    missing = [
        field_name
        for field_name in required_fields
        if not getattr(plugin_config, field_name, None)
    ]
    if not plugin_config.javplay_flaresolverr_url and not plugin_config.javplay_proxy_http:
        missing.append("javplay_flaresolverr_url or javplay_proxy_http")
    if missing:
        logger.warning(
            "JavPlay required config is incomplete. "
            "Please set these fields in your NoneBot .env: "
            + ", ".join(missing)
        )


_warn_missing_required_config()


def _real_media_roots_for_finish() -> Optional[list[str]]:
    if _storage_mode() != "115_mount":
        return None
    root = plugin_config.javplay_115_mount_jellyfin_path
    return [root] if root else None


async def _queue_download(video_id: str, user_id: Optional[str], background_tasks: BackgroundTasks, source: str) -> bool:
    video_id = _extract_video_id(video_id)
    if not video_id:
        return False

    async with task_lock:
        existing = active_downloads.get(video_id)
        if existing:
            if user_id and not existing.get("user_id"):
                existing["user_id"] = user_id
            already_running = True
        else:
            active_downloads[video_id] = {"user_id": user_id, "status": "queued", "gid": None}
            already_running = False

    if already_running:
        logger.info(f"Download for {video_id} is already queued/running, source={source}")
        return False

    logger.info(f"Queueing download for {video_id}, source={source}")
    background_tasks.add_task(background_download_task, video_id, user_id)
    return True


def _same_video_id(left: str, right: str) -> bool:
    return left.lower().replace("-", "") == right.lower().replace("-", "")


def _same_user_id(left: str, right: str) -> bool:
    return (left or "").lower().replace("-", "") == (right or "").lower().replace("-", "")


def _normalise_media_path(value: str) -> str:
    return (value or "").replace("\\", "/").rstrip("/")


def _path_under_root(path: str, root: str) -> bool:
    path = _normalise_media_path(path).lower()
    root = _normalise_media_path(root).lower()
    if not path or not root:
        return False
    return path == root or path.startswith(root + "/")


def _jellyfin_item_paths(item: dict) -> list[str]:
    paths = []
    path = item.get("Path")
    if path:
        paths.append(path)
    for source in item.get("MediaSources") or []:
        path = source.get("Path")
        if path:
            paths.append(path)
    return paths


def _is_virtual_javplay_item(item: dict) -> bool:
    for path in _jellyfin_item_paths(item or {}):
        lower_path = path.lower()
        if lower_path.endswith(".strm") or "/trigger/" in lower_path or "trigger.mp4" in lower_path or "wait.mp4" in lower_path:
            return True
    return False


def _is_real_javplay_item(item: dict) -> bool:
    for path in _jellyfin_item_paths(item or {}):
        lower_path = path.lower()
        if lower_path.endswith(".strm") or "/trigger/" in lower_path or "trigger.mp4" in lower_path or "wait.mp4" in lower_path:
            continue
        if lower_path.endswith((".mp4", ".mkv", ".avi", ".wmv", ".mov", ".ts", ".m2ts", ".flv", ".iso", ".rmvb")):
            return True
    return False


def _is_javplay_jellyfin_item(item: dict) -> bool:
    media_root = plugin_config.javplay_jellyfin_media_path
    if not media_root:
        logger.warning("javplay_jellyfin_media_path is not configured; refusing to infer JavPlay playback.")
        return False

    for path in _jellyfin_item_paths(item or {}):
        if _path_under_root(path, media_root):
            return True
    return False


def _find_active_jav_playback(expected_video_id: str = "", expected_user_id: str = "") -> tuple[str, Optional[str]]:
    expected_video_id = _extract_video_id(expected_video_id) if expected_video_id else ""
    sessions = get_active_sessions(
        plugin_config.javplay_jellyfin_url,
        plugin_config.javplay_jellyfin_api_key,
    )
    for session in sessions:
        if expected_user_id and not _same_user_id(session.get("UserId"), expected_user_id):
            continue

        item = session.get("NowPlayingItem") or {}
        if not _is_javplay_jellyfin_item(item):
            continue
        if not _is_virtual_javplay_item(item):
            continue

        candidates = [
            item.get("Name", ""),
            item.get("OriginalTitle", ""),
            item.get("Path", ""),
            item.get("FileName", ""),
        ]
        for candidate in candidates:
            video_id = _infer_video_id(candidate)
            if video_id and (not expected_video_id or _same_video_id(video_id, expected_video_id)):
                return video_id, session.get("UserId")
    return "", None


def _message_only_virtual_response():
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


async def _queue_from_virtual_stream(
    video_id: str,
    background_tasks: BackgroundTasks,
    source: str,
) -> bool:
    active_video_id, user_id = await asyncio.to_thread(_find_active_jav_playback, video_id)
    if not active_video_id:
        logger.info(
            f"Virtual stream requested for {video_id} without queue; "
            "no matching active Jellyfin playback session."
        )
        return False

    logger.info(f"Confirmed virtual playback from active session: {active_video_id}")
    return await _queue_download(active_video_id, user_id, background_tasks, source)


@app.get("/trigger/{video_id}.mp4")
async def trigger_virtual_video(video_id: str, background_tasks: BackgroundTasks):
    await _queue_from_virtual_stream(video_id, background_tasks, "virtual-stream")
    return _message_only_virtual_response()


@app.get("/trigger.mp4")
async def trigger_virtual_video_query(request: Request, background_tasks: BackgroundTasks):
    video_id = request.query_params.get("video_id") or request.query_params.get("id") or ""
    if video_id:
        await _queue_from_virtual_stream(video_id, background_tasks, "virtual-stream")
    else:
        logger.info("Virtual stream requested without video_id; no download queued.")
    return _message_only_virtual_response()


@app.get("/wait.mp4")
async def wait_video(request: Request, background_tasks: BackgroundTasks):
    video_id = request.query_params.get("video_id") or request.query_params.get("id") or ""
    if video_id:
        await _queue_from_virtual_stream(video_id, background_tasks, "wait-video-legacy")
    else:
        video_id, user_id = await asyncio.to_thread(_find_active_jav_playback)
        if video_id:
            logger.info(f"Inferred Jellyfin playback from active session: {video_id}")
            await _queue_download(video_id, user_id, background_tasks, "wait-video-legacy")
        else:
            logger.info("Legacy wait.mp4 requested without video_id; no download queued.")

    return _message_only_virtual_response()


@app.get("/wait/{video_id}.mp4")
async def wait_video_with_id(video_id: str, background_tasks: BackgroundTasks):
    await _queue_from_virtual_stream(video_id, background_tasks, "wait-video-path-legacy")
    return _message_only_virtual_response()

MEDIA_CACHE_EXTS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".wmv",
    ".mov",
    ".ts",
    ".m2ts",
    ".flv",
    ".iso",
    ".rmvb",
    ".ass",
    ".srt",
    ".ssa",
    ".vtt",
    ".aria2",
}

LOCAL_COMPLETE_VIDEO_EXTS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".wmv",
    ".mov",
    ".ts",
    ".m2ts",
    ".flv",
    ".iso",
    ".rmvb",
}

active_downloads: Dict[str, Dict[str, Any]] = {}
gid_to_video_id: Dict[str, str] = {}
task_lock = asyncio.Lock()
manual_crawl_lock = asyncio.Lock()

update_jav = on_command(
    "更新jav",
    aliases={"更新JAV", "更新Jav"},
    permission=JAVPLAY_ADMIN_PERMISSION,
    priority=5,
    block=True,
)

full_scan_jav = on_command(
    "完全扫描jav",
    aliases={"完全扫描JAV", "完全扫描Jav"},
    permission=JAVPLAY_ADMIN_PERMISSION,
    priority=5,
    block=True,
)


def _extract_video_id(item_name: str) -> str:
    match = re.search(r"[A-Za-z]{2,10}[-_ ]?\d{2,6}", item_name or "")
    if not match:
        return (item_name or "").strip()
    code = match.group(0).upper().replace("_", "-").replace(" ", "-")
    if "-" not in code:
        letters = re.match(r"[A-Z]+", code)
        if letters:
            code = f"{letters.group(0)}-{code[len(letters.group(0)):]}"
    return code


def _infer_video_id(value: str) -> str:
    match = re.search(r"[A-Za-z]{2,10}[-_ ]?\d{2,6}", value or "")
    if not match:
        return ""
    return _extract_video_id(match.group(0))


def _full_scan_state_file() -> str:
    state_file = plugin_config.javplay_full_scan_state_file
    if os.path.isabs(state_file):
        return state_file
    return os.path.join(os.path.dirname(__file__), state_file)


def _read_full_scan_page() -> int:
    default_page = max(1, plugin_config.javplay_crawl_start_page)
    state_file = _full_scan_state_file()
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("completed"):
            return default_page
        return max(1, int(data.get("next_page", default_page)))
    except Exception:
        return default_page


def _write_full_scan_page(next_page: int, completed: bool = False, reason: str = "") -> None:
    max_page = max(1, plugin_config.javplay_crawl_max_page)
    start_page = max(1, plugin_config.javplay_crawl_start_page)
    if next_page > max_page:
        completed = True
        reason = reason or "max_page_reached"
        next_page = start_page

    state_file = _full_scan_state_file()
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    data = {
        "next_page": next_page,
        "completed": bool(completed),
        "reason": reason,
        "updated_at": int(time.time()),
    }
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _claim_full_scan_window() -> int:
    return _read_full_scan_page()


def _mark_full_scan_window_done(start_page: int, pages_completed: int) -> None:
    _write_full_scan_page(start_page + max(1, pages_completed))


def _format_crawl_result(stats: dict) -> str:
    return (
        f"新增 {stats.get('added', 0)} 个虚拟影片，"
        f"本地已有跳过 {stats.get('existing', 0)} 个影片，"
        f"无效跳过 {stats.get('invalid', 0)} 个，"
        f"完成页数 {stats.get('pages_completed', 0)}。"
    )


def _parse_full_scan_start_page(raw_arg: str) -> Optional[int]:
    raw_arg = (raw_arg or "").strip()
    max_pages = max(1, plugin_config.javplay_crawl_max_page)

    if not raw_arg:
        return _claim_full_scan_window()
    if not raw_arg.isdigit():
        return None
    return min(max(1, int(raw_arg)), max_pages)


def _new_full_scan_stats(start_page: int) -> dict:
    return {
        "added": 0,
        "existing": 0,
        "invalid": 0,
        "pages_requested": 0,
        "pages_completed": 0,
        "start_page": start_page,
        "end_page": start_page,
        "last_page": 0,
        "stopped_reason": "",
    }


def _merge_full_scan_stats(total: dict, stats: dict) -> None:
    for key in ("added", "existing", "invalid", "pages_requested", "pages_completed"):
        total[key] = total.get(key, 0) + stats.get(key, 0)
    total["end_page"] = max(total.get("end_page", 0), stats.get("end_page", 0))
    total["last_page"] = max(total.get("last_page", 0), stats.get("last_page", 0))
    total["stopped_reason"] = stats.get("stopped_reason") or total.get("stopped_reason", "")


async def _run_full_scan_until_done(start_page: int) -> tuple[dict, bool, int, str]:
    max_page = max(1, plugin_config.javplay_crawl_max_page)
    batch_size = max(1, plugin_config.javplay_full_scan_pages_per_run)
    current_page = max(1, min(start_page, max_page))
    total = _new_full_scan_stats(current_page)
    completed = False
    reason = ""

    while current_page <= max_page:
        pages = min(batch_size, max_page - current_page + 1)
        logger.info(f"Full JavDB scan batch: pages {current_page}-{current_page + pages - 1}")
        stats = await asyncio.to_thread(
            build_phantom_library,
            pages,
            plugin_config.javplay_proxy_http,
            _media_host_path(),
            plugin_config.javplay_flaresolverr_url,
            plugin_config.javplay_strm_url,
            plugin_config.javplay_flaresolverr_proxy,
            current_page,
        )
        _merge_full_scan_stats(total, stats)

        pages_completed = stats.get("pages_completed", 0)
        if pages_completed > 0:
            current_page += pages_completed
            _write_full_scan_page(current_page, completed=False)

        stopped_reason = stats.get("stopped_reason") or ""
        if stopped_reason == "completed" and pages_completed >= pages:
            continue

        if stopped_reason == "no_items":
            completed = total.get("pages_completed", 0) > 0
            reason = "no_items"
            break

        reason = stopped_reason or "no_progress"
        break

    if current_page > max_page:
        completed = True
        reason = reason or "max_page_reached"

    _write_full_scan_page(current_page, completed=completed, reason=reason)
    total["stopped_reason"] = reason or total.get("stopped_reason", "completed")
    return total, completed, _read_full_scan_page(), total["stopped_reason"]


def _webhook_authorized(request: Request) -> bool:
    token = plugin_config.javplay_webhook_token
    if not token:
        return True
    return (
        request.headers.get("X-JavPlay-Token") == token
        or request.query_params.get("token") == token
    )


async def send_jellyfin_msg(message: str, user_id: Optional[str] = None) -> bool:
    return await asyncio.to_thread(
        send_jellyfin_notification,
        plugin_config.javplay_jellyfin_url,
        plugin_config.javplay_jellyfin_api_key,
        message,
        user_id,
        plugin_config.javplay_jellyfin_message_timeout_ms,
    )


async def _set_task_failed(video_id: str) -> None:
    async with task_lock:
        task = active_downloads.pop(video_id, None)
        if task and task.get("gid"):
            gid_to_video_id.pop(task["gid"], None)


def _aria2_completed_file_path(status: Optional[dict]) -> str:
    if not status:
        return ""

    for file_info in status.get("files") or []:
        path = file_info.get("path") or ""
        if path:
            return path
    return ""


def _find_local_complete_candidate(video_id: str) -> tuple[str, int]:
    video_dir = os.path.abspath(os.path.join(_media_host_path(), video_id))
    media_root = os.path.abspath(_media_host_path())
    if not video_dir.startswith(media_root + os.sep) or not os.path.isdir(video_dir):
        return "", 0

    min_size = max(0, int(plugin_config.javplay_local_complete_min_size_mb or 0)) * 1024 * 1024
    candidates = []
    for file_name in os.listdir(video_dir):
        file_path = os.path.join(video_dir, file_name)
        if not os.path.isfile(file_path):
            continue

        ext = os.path.splitext(file_name)[1].lower()
        if ext not in LOCAL_COMPLETE_VIDEO_EXTS:
            continue
        if os.path.exists(file_path + ".aria2"):
            continue

        try:
            size = os.path.getsize(file_path)
        except OSError:
            continue
        if size < min_size:
            continue

        candidates.append((size, file_path))

    if not candidates:
        return "", 0

    candidates.sort(reverse=True)
    size, path = candidates[0]
    return path, size


async def _finish_download(
    video_id: str,
    gid: str,
    user_id: Optional[str],
    file_path: str = "",
    real_media_roots: Optional[list[str]] = None,
) -> bool:
    async with task_lock:
        task = active_downloads.get(video_id)
        if gid:
            gid_to_video_id.pop(gid, None)
        if task and not user_id:
            user_id = task.get("user_id")
        if task:
            task["status"] = "refreshing"
            task["gid"] = None

    if not task:
        logger.info(f"Download for {video_id} was already finalized or is no longer active.")
        return False

    await send_jellyfin_msg(f"影片 {video_id} 下载完成！正在刷新该影片元数据，请稍候...", user_id)

    refresh_success = await asyncio.to_thread(
        refresh_jellyfin_item_by_video_id,
        plugin_config.javplay_jellyfin_url,
        plugin_config.javplay_jellyfin_api_key,
        video_id,
        real_media_roots,
    )

    real_item = await asyncio.to_thread(
        wait_for_real_jellyfin_item,
        plugin_config.javplay_jellyfin_url,
        plugin_config.javplay_jellyfin_api_key,
        video_id,
        plugin_config.javplay_jellyfin_item_refresh_wait_seconds,
        plugin_config.javplay_jellyfin_item_refresh_interval_seconds,
        real_media_roots,
    )

    if not real_item:
        logger.info(f"Real item for {video_id} not visible after item refresh; triggering full library refresh fallback.")
        refresh_success = await asyncio.to_thread(
            refresh_jellyfin_library,
            plugin_config.javplay_jellyfin_url,
            plugin_config.javplay_jellyfin_api_key,
        )
        real_item = await asyncio.to_thread(
            wait_for_real_jellyfin_item,
            plugin_config.javplay_jellyfin_url,
            plugin_config.javplay_jellyfin_api_key,
            video_id,
            plugin_config.javplay_jellyfin_item_refresh_wait_seconds,
            plugin_config.javplay_jellyfin_item_refresh_interval_seconds,
            real_media_roots,
        )

    if real_item:
        await asyncio.to_thread(
            remove_phantom_video,
            _media_host_path(),
            video_id,
        )
        if real_item.get("Id"):
            await asyncio.to_thread(
                refresh_jellyfin_item,
                plugin_config.javplay_jellyfin_url,
                plugin_config.javplay_jellyfin_api_key,
                real_item["Id"],
            )

    if real_item and refresh_success:
        await send_jellyfin_msg(f"影片 {video_id} 已更新完成，现在可以重新进入播放。", user_id)
    elif real_item:
        await send_jellyfin_msg(f"影片 {video_id} 已在媒体库中可见，但元数据刷新可能仍在后台进行。", user_id)
    else:
        async with task_lock:
            pending_task = active_downloads.get(video_id)
            if pending_task:
                pending_task["status"] = "downloaded_pending_scan"
                pending_task["file_path"] = file_path
        await send_jellyfin_msg(
            f"影片 {video_id} 已下载完成，但 Jellyfin 暂未扫描到真实文件，请稍后再试。",
            user_id,
        )
        return False

    async with task_lock:
        active_downloads.pop(video_id, None)

    logger.info(f"Download finalized for {video_id}: {file_path or gid}")
    return True


async def _refresh_library_and_notify(message_prefix: str, user_id: Optional[str] = None) -> None:
    refresh_success = await asyncio.to_thread(
        refresh_jellyfin_library,
        plugin_config.javplay_jellyfin_url,
        plugin_config.javplay_jellyfin_api_key,
    )

    if refresh_success:
        await send_jellyfin_msg(f"{message_prefix}媒体库刷新已触发。", user_id)
    else:
        await send_jellyfin_msg(f"{message_prefix}媒体库刷新失败，请稍后手动刷新。", user_id)


async def _monitor_aria2_download(video_id: str, gid: str, user_id: Optional[str]) -> None:
    interval = max(5, int(plugin_config.javplay_aria2_poll_interval_seconds or 30))
    timeout = max(1, int(plugin_config.javplay_aria2_poll_timeout_hours or 24)) * 3600
    deadline = time.time() + timeout
    last_local_candidate = ("", 0)

    logger.info(f"Monitoring Aria2 task {gid} for {video_id}, interval={interval}s, timeout={timeout}s")
    while time.time() < deadline:
        await asyncio.sleep(interval)

        async with task_lock:
            task = active_downloads.get(video_id)
            if not task or task.get("gid") != gid:
                logger.info(f"Stop monitoring {video_id}; task is no longer active.")
                return

        status = await asyncio.to_thread(
            tell_status,
            plugin_config.javplay_aria2_rpc,
            plugin_config.javplay_aria2_secret,
            gid,
        )
        state = (status or {}).get("status")
        if not state:
            local_path, local_size = await asyncio.to_thread(_find_local_complete_candidate, video_id)
            if local_path:
                if last_local_candidate == (local_path, local_size):
                    logger.info(
                        f"Aria2 status unavailable for {video_id}, "
                        f"but local file is stable: {local_path} ({local_size} bytes)"
                    )
                    await _finish_download(video_id, gid, user_id, local_path)
                    return
                last_local_candidate = (local_path, local_size)
                logger.info(
                    f"Aria2 status unavailable for {video_id}; "
                    f"found local candidate, waiting one more poll for stability: {local_path}"
                )
            continue

        if state == "complete":
            await _finish_download(video_id, gid, user_id, _aria2_completed_file_path(status))
            return

        if state in {"error", "removed"}:
            error_message = (status or {}).get("errorMessage") or state
            logger.error(f"Aria2 task failed for {video_id}: {error_message}")
            await send_jellyfin_msg(f"影片 {video_id} 下载失败：{error_message}", user_id)
            await _set_task_failed(video_id)
            return

    logger.warning(f"Aria2 monitor timed out for {video_id}, gid={gid}; task remains active.")


def _parse_manual_crawl_pages(raw_arg: str) -> Optional[int]:
    raw_arg = (raw_arg or "").strip()
    default_pages = max(1, plugin_config.javplay_manual_crawl_pages)
    max_pages = max(1, plugin_config.javplay_manual_crawl_max_pages)

    if not raw_arg:
        return min(default_pages, max_pages)

    if not raw_arg.isdigit():
        return None

    return min(max(1, int(raw_arg)), max_pages)


@update_jav.handle()
async def handle_update_jav(args=CommandArg()):
    pages = _parse_manual_crawl_pages(args.extract_plain_text())
    if pages is None:
        await update_jav.finish("格式：更新jav 或 更新jav 2")

    if manual_crawl_lock.locked():
        await update_jav.finish("JavDB 更新任务正在运行，请稍后再试。")

    async with manual_crawl_lock:
        start_page = max(1, plugin_config.javplay_crawl_start_page)
        end_page = start_page + pages - 1
        await update_jav.send(f"开始更新 JavDB 测试库，本次爬取第 {start_page}-{end_page} 页。")

        stats = await asyncio.to_thread(
            build_phantom_library,
            pages,
            plugin_config.javplay_proxy_http,
            _media_host_path(),
            plugin_config.javplay_flaresolverr_url,
            plugin_config.javplay_strm_url,
            plugin_config.javplay_flaresolverr_proxy,
            start_page,
        )

        refresh_success = False
        if stats.get("added", 0) > 0:
            refresh_success = await asyncio.to_thread(
                refresh_jellyfin_library,
                plugin_config.javplay_jellyfin_url,
                plugin_config.javplay_jellyfin_api_key,
            )

        result = _format_crawl_result(stats)
        if stats.get("added", 0) <= 0:
            await update_jav.finish(f"更新完成，第 {start_page}-{end_page} 页没有新增虚拟影片。{result}可能是已存在、页面为空或被风控。")
        if refresh_success:
            await update_jav.finish(f"更新完成，第 {start_page}-{end_page} 页{result}媒体库刷新已触发。")
        await update_jav.finish(f"更新完成，第 {start_page}-{end_page} 页{result}但媒体库刷新失败，请手动刷新。")


@full_scan_jav.handle()
async def handle_full_scan_jav(args=CommandArg()):
    start_page = _parse_full_scan_start_page(args.extract_plain_text())
    if start_page is None:
        await full_scan_jav.finish("格式：完全扫描jav 或 完全扫描jav 120（从第 120 页开始持续扫描）")

    if manual_crawl_lock.locked():
        await full_scan_jav.finish("JavDB 更新任务正在运行，请稍后再试。")

    async with manual_crawl_lock:
        await full_scan_jav.send(
            f"开始完整扫描 JavDB，将从第 {start_page} 页持续扫描，"
            f"直到页面为空或达到最大页 {max(1, plugin_config.javplay_crawl_max_page)}。"
        )
        stats, completed, next_page, reason = await _run_full_scan_until_done(start_page)

        refresh_success = False
        if stats.get("added", 0) > 0:
            refresh_success = await asyncio.to_thread(
                refresh_jellyfin_library,
                plugin_config.javplay_jellyfin_url,
                plugin_config.javplay_jellyfin_api_key,
            )

        result = _format_crawl_result(stats)
        suffix = "媒体库刷新已触发。" if refresh_success else "没有新增或媒体库刷新失败。"
        state_text = "整个数据库扫描已完成" if completed else f"扫描中断，下次从第 {next_page} 页继续"
        await full_scan_jav.finish(
            f"完整扫描结束，从第 {start_page} 页开始{result}"
            f"{state_text}（原因：{reason or 'completed'}），{suffix}"
        )


def cleanup_local_downloads(has_active_downloads: bool = False) -> int:
    if not plugin_config.javplay_cleanup_enabled:
        return 0

    download_dir = _media_host_path(plugin_config.javplay_cache_host_path or plugin_config.javplay_db_path)
    if not download_dir or not os.path.isdir(download_dir):
        return 0

    normalised_dir = os.path.abspath(download_dir)
    if normalised_dir in (os.path.abspath(os.sep), os.path.expanduser("~")):
        logger.error(f"Refusing to cleanup unsafe download directory: {normalised_dir}")
        return 0

    if has_active_downloads:
        logger.info("Skip cleanup because active downloads are running.")
        return 0

    cutoff = time.time() - (plugin_config.javplay_cleanup_keep_hours * 3600)
    deleted = 0
    restored_video_ids = set()

    for root, dirs, files in os.walk(normalised_dir, topdown=False):
        for file_name in files:
            file_path = os.path.join(root, file_name)
            try:
                ext = os.path.splitext(file_name)[1].lower()
                if ext not in MEDIA_CACHE_EXTS:
                    continue
                if os.path.getmtime(file_path) >= cutoff:
                    continue

                video_id = _infer_video_id(file_name)
                os.remove(file_path)
                deleted += 1
                if video_id and ext not in {".ass", ".srt", ".ssa", ".vtt", ".aria2"}:
                    restored_video_ids.add(video_id)
                logger.info(f"Cleaned old download file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup file {file_path}: {e}")

        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
            except Exception:
                pass

    if deleted:
        logger.info(f"Download cleanup finished, removed {deleted} files.")
        for video_id in restored_video_ids:
            try:
                create_phantom_video(
                    _media_host_path(),
                    video_id,
                    plugin_config.javplay_strm_url,
                )
            except Exception as e:
                logger.warning(f"Failed to restore phantom video {video_id}: {e}")
    return deleted


async def background_download_task(video_id: str, user_id: Optional[str] = None):
    logger.info(f"Starting background download task for {video_id}")
    await send_jellyfin_msg(f"正在为您检索并下载影片 {video_id}，请耐心等待...", user_id)

    magnet = await search_magnet(video_id, plugin_config)
    if not magnet:
        await send_jellyfin_msg(f"未能找到影片 {video_id} 的磁力链接。", user_id)
        await _set_task_failed(video_id)
        return

    if _storage_mode() == "115_mount":
        logger.info("Using 115 mount mode for offline downloading...")
        mount_result = await asyncio.to_thread(
            download_to_115_mount,
            plugin_config.javplay_115_cookie,
            magnet,
            video_id,
            plugin_config.javplay_115_savepath,
            plugin_config.javplay_115_min_video_size_mb,
            plugin_config.javplay_115_junk_keywords,
            plugin_config.javplay_115_require_wanted_selection,
            plugin_config.javplay_115_temp_savepath,
        )

        if not mount_result:
            await send_jellyfin_msg(
                f"影片 {video_id} 添加 115 挂载任务失败，请检查 115 文件选择或挂载状态。",
                user_id,
            )
            await _set_task_failed(video_id)
            return

        async with task_lock:
            active_downloads.setdefault(video_id, {})["user_id"] = user_id
            active_downloads[video_id]["status"] = "refreshing"
            active_downloads[video_id]["gid"] = None

        cloud_path = mount_result.get("cloud_path") or ""
        logger.info(f"115 mount task for {video_id} completed in cloud: {cloud_path}")
        await asyncio.to_thread(
            remove_phantom_video,
            _media_host_path(),
            video_id,
        )
        await send_jellyfin_msg(f"影片 {video_id} 已保存到 115 挂载目录，正在刷新媒体库。", user_id)
        await _finish_download(video_id, "", user_id, cloud_path, _real_media_roots_for_finish())
        return

    logger.info("Using 115 + Aria2 cache mode for offline downloading...")
    gid = await asyncio.to_thread(
        download_via_115,
        plugin_config.javplay_115_cookie,
        magnet,
        video_id,
        plugin_config.javplay_aria2_rpc,
        plugin_config.javplay_aria2_secret,
        plugin_config.javplay_aria2_dir,
        plugin_config.javplay_115_savepath,
    )

    if not gid:
        await send_jellyfin_msg(f"影片 {video_id} 添加下载任务失败，请检查 115 或 Aria2 状态。", user_id)
        await _set_task_failed(video_id)
        return

    async with task_lock:
        active_downloads.setdefault(video_id, {})["gid"] = gid
        active_downloads[video_id]["user_id"] = user_id
        active_downloads[video_id]["status"] = "downloading"
        gid_to_video_id[gid] = video_id

    logger.info(f"Task for {video_id} successfully pushed to Aria2. GID: {gid}")
    await send_jellyfin_msg(f"影片 {video_id} 磁力链已成功推送到下载器。", user_id)
    await _monitor_aria2_download(video_id, gid, user_id)


@app.post("/webhook/jellyfin")
async def handle_jellyfin_webhook(request: Request, background_tasks: BackgroundTasks):
    if not _webhook_authorized(request):
        return {"status": "forbidden"}

    try:
        data = await request.json()
        event_type = str(data.get("NotificationType") or data.get("Event") or data.get("Type") or "")
        normalised_event_type = re.sub(r"[^a-z0-9]", "", event_type.lower())
        if normalised_event_type not in {"playbackstart", "playbackstarted"}:
            logger.info(f"Ignored Jellyfin webhook event: {event_type or 'unknown'}")
            return {"status": "ignored", "reason": "Not PlaybackStart", "event_type": event_type}

        item = data.get("Item") if isinstance(data.get("Item"), dict) else {}
        item_name = (
            data.get("ItemName")
            or data.get("Name")
            or data.get("Title")
            or item.get("Name")
            or item.get("OriginalTitle")
            or item.get("Path")
            or ""
        )
        video_id = _extract_video_id(item_name)
        if not video_id:
            logger.info(f"Ignored Jellyfin PlaybackStart webhook without video id: {data}")
            return {"status": "ignored", "reason": "No ItemName"}

        user_id = data.get("UserId")
        if _is_javplay_jellyfin_item(item) and _is_real_javplay_item(item):
            logger.info(
                f"Ignored Jellyfin PlaybackStart for real JavPlay media: "
                f"video_id={video_id}, item_name={item_name!r}, user_id={user_id or ''}"
            )
            return {"status": "ignored", "reason": "Real media playback", "video_id": video_id}

        if not _is_javplay_jellyfin_item(item):
            active_video_id, active_user_id = await asyncio.to_thread(
                _find_active_jav_playback,
                video_id,
                user_id or "",
            )
            if not active_video_id:
                logger.info(
                    f"Ignored Jellyfin PlaybackStart outside JavPlay media path: "
                    f"video_id={video_id}, item_name={item_name!r}, user_id={user_id or ''}"
                )
                return {"status": "ignored", "reason": "Not JavPlay media library", "video_id": video_id}
            video_id = active_video_id
            user_id = user_id or active_user_id
        elif not _is_virtual_javplay_item(item):
            logger.info(
                f"Ignored Jellyfin PlaybackStart for non-virtual JavPlay item: "
                f"video_id={video_id}, item_name={item_name!r}, user_id={user_id or ''}"
            )
            return {"status": "ignored", "reason": "Not virtual JavPlay media", "video_id": video_id}

        queued = await _queue_download(video_id, user_id, background_tasks, "jellyfin-webhook")
        if not queued:
            await send_jellyfin_msg(f"影片 {video_id} 已在下载队列中，请稍后。", user_id)

        return {"status": "processing", "video_id": video_id, "queued": queued}

    except Exception as e:
        logger.error(f"Error handling Jellyfin webhook: {e}")
        return {"error": str(e)}


class Aria2WebhookPayload(BaseModel):
    gid: str
    file_path: str = ""


@app.post("/webhook/aria2")
async def handle_aria2_webhook(payload: Aria2WebhookPayload, request: Request):
    if not _webhook_authorized(request):
        return {"status": "forbidden"}

    try:
        logger.info(f"Received Aria2 webhook: {payload}")
        file_name = os.path.basename(payload.file_path).lower() if payload.file_path else payload.gid

        async with task_lock:
            completed_video_id = gid_to_video_id.pop(payload.gid, None)
            if not completed_video_id:
                for vid, task in active_downloads.items():
                    if task.get("gid") == payload.gid:
                        completed_video_id = vid
                        break
            if not completed_video_id:
                for vid in active_downloads:
                    if vid.lower() in file_name or vid.lower().replace("-", "") in file_name:
                        completed_video_id = vid
                        break

            task = active_downloads.get(completed_video_id) if completed_video_id else None
            user_id = task.get("user_id") if task else None

        if completed_video_id:
            await _finish_download(completed_video_id, payload.gid, user_id, payload.file_path)
        else:
            await send_jellyfin_msg(f"下载任务已完成: {file_name}。正在刷新媒体库...")
            refresh_success = await asyncio.to_thread(
                refresh_jellyfin_library,
                plugin_config.javplay_jellyfin_url,
                plugin_config.javplay_jellyfin_api_key,
            )
            if refresh_success:
                await send_jellyfin_msg("媒体库刷新成功！您现在可以退出去重新点进影片观看了。", user_id)
            else:
                await send_jellyfin_msg("媒体库刷新失败，请稍后手动刷新。", user_id)

        return {"status": "ok", "video_id": completed_video_id}

    except Exception as e:
        logger.error(f"Error handling Aria2 webhook: {e}")
        return {"error": str(e)}


if scheduler:

    @scheduler.scheduled_job(
        "cron",
        hour=plugin_config.javplay_daily_crawl_hour,
        minute=plugin_config.javplay_daily_crawl_minute,
        timezone=plugin_config.javplay_scheduler_timezone,
        id="javplay_daily_library_build",
    )
    async def scheduled_library_build():
        if manual_crawl_lock.locked():
            logger.info("Skip daily JavDB crawl because another crawl task is running.")
            return

        logger.info(
            "Starting daily JavDB phantom library build task "
            f"at {plugin_config.javplay_daily_crawl_hour:02d}:"
            f"{plugin_config.javplay_daily_crawl_minute:02d} "
            f"{plugin_config.javplay_scheduler_timezone}..."
        )
        async with manual_crawl_lock:
            pages = max(1, plugin_config.javplay_crawl_pages_daily)
            start_page = max(1, plugin_config.javplay_crawl_start_page)
            end_page = start_page + pages - 1
            logger.info(f"Daily JavDB crawl window: pages {start_page}-{end_page}")
            stats = await asyncio.to_thread(
                build_phantom_library,
                pages,
                plugin_config.javplay_proxy_http,
                _media_host_path(),
                plugin_config.javplay_flaresolverr_url,
                plugin_config.javplay_strm_url,
                plugin_config.javplay_flaresolverr_proxy,
                start_page,
            )
        if stats.get("added", 0) > 0:
            logger.info(
                f"Daily crawl finished. Added {stats.get('added', 0)} items. Refreshing Jellyfin..."
            )
            await asyncio.to_thread(
                refresh_jellyfin_library,
                plugin_config.javplay_jellyfin_url,
                plugin_config.javplay_jellyfin_api_key,
            )

    @scheduler.scheduled_job(
        "interval",
        minutes=plugin_config.javplay_cleanup_interval_minutes,
        id="javplay_local_download_cleanup",
    )
    async def scheduled_local_cleanup():
        async with task_lock:
            has_active = bool(active_downloads)
        deleted = await asyncio.to_thread(cleanup_local_downloads, has_active)
        if deleted:
            await asyncio.to_thread(
                refresh_jellyfin_library,
                plugin_config.javplay_jellyfin_url,
                plugin_config.javplay_jellyfin_api_key,
            )

