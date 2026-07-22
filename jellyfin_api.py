import requests
from nonebot import logger


VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".wmv", ".mov", ".ts", ".m2ts", ".flv", ".iso", ".rmvb")


def _headers(api_key: str) -> dict:
    return {"X-Emby-Token": api_key}


def _normalise_code(value: str) -> str:
    return (value or "").lower().replace("-", "").replace("_", "").replace(" ", "")


def _normalise_user_id(value: str) -> str:
    return (value or "").lower().replace("-", "")


def _item_paths(item: dict) -> list[str]:
    paths = []
    path = item.get("Path")
    if path:
        paths.append(path)
    for source in item.get("MediaSources") or []:
        path = source.get("Path")
        if path:
            paths.append(path)
    return paths


def _normalise_media_path(value: str) -> str:
    return (value or "").replace("\\", "/").rstrip("/").lower()


def _path_under_root(path: str, root: str) -> bool:
    path = _normalise_media_path(path)
    root = _normalise_media_path(root)
    if not path or not root:
        return False
    return path == root or path.startswith(root + "/")


def _item_under_roots(item: dict, media_roots: list[str] = None) -> bool:
    roots = [root for root in (media_roots or []) if root]
    if not roots:
        return True
    return any(
        _path_under_root(path, root)
        for path in _item_paths(item)
        for root in roots
    )


def _is_real_media_item(item: dict) -> bool:
    for path in _item_paths(item):
        lower_path = path.lower()
        if lower_path.endswith(".strm") or "wait.mp4" in lower_path:
            continue
        if lower_path.endswith(VIDEO_EXTS):
            return True
    return False


def refresh_jellyfin_library(base_url: str, api_key: str):
    """
    Call Jellyfin API to refresh the entire library.
    """
    if not base_url or not api_key:
        logger.error("Jellyfin URL or API key missing.")
        return False
        
    # Endpoint to refresh library
    url = f"{base_url}/Library/Refresh"
    headers = {
        "X-Emby-Token": api_key
    }
    
    try:
        # Jellyfin API requires POST for Library/Refresh
        resp = requests.post(url, headers=headers, timeout=10)
        # 204 No Content is usually returned on success
        if resp.status_code in (200, 204):
            logger.info("Jellyfin library refresh triggered successfully.")
            return True
        else:
            logger.error(f"Failed to refresh Jellyfin library: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Failed to refresh Jellyfin library: {e}")
        return False


def find_jellyfin_item(base_url: str, api_key: str, video_id: str, media_roots: list[str] = None):
    if not base_url or not api_key or not video_id:
        return None

    headers = _headers(api_key)
    params = {
        "Recursive": "true",
        "SearchTerm": video_id,
        "IncludeItemTypes": "Movie,Video",
        "Fields": "Path,MediaSources",
        "Limit": 20,
    }
    target = _normalise_code(video_id)

    try:
        resp = requests.get(f"{base_url}/Items", headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Failed to search Jellyfin item {video_id}: {resp.status_code} - {resp.text[:200]}")
            return None

        items = resp.json().get("Items") or []
        candidates = []
        for item in items:
            if media_roots and not _item_under_roots(item, media_roots):
                continue
            haystack = " ".join(
                [
                    item.get("Name") or "",
                    item.get("OriginalTitle") or "",
                    " ".join(_item_paths(item)),
                ]
            )
            if target and target in _normalise_code(haystack):
                score = 10
                if _is_real_media_item(item):
                    score += 100
                candidates.append((score, item))

        if not candidates:
            return None

        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return candidates[0][1]
    except Exception as e:
        logger.warning(f"Failed to search Jellyfin item {video_id}: {e}")
        return None


def refresh_jellyfin_item(base_url: str, api_key: str, item_id: str) -> bool:
    if not base_url or not api_key or not item_id:
        return False

    params = {
        "Recursive": "false",
        "MetadataRefreshMode": "FullRefresh",
        "ImageRefreshMode": "FullRefresh",
        "ReplaceAllMetadata": "false",
        "ReplaceAllImages": "false",
    }

    try:
        resp = requests.post(
            f"{base_url}/Items/{item_id}/Refresh",
            headers=_headers(api_key),
            params=params,
            timeout=10,
        )
        if resp.status_code in (200, 204):
            logger.info(f"Jellyfin item refresh triggered successfully: {item_id}")
            return True
        logger.warning(f"Failed to refresh Jellyfin item {item_id}: {resp.status_code} - {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Failed to refresh Jellyfin item {item_id}: {e}")
        return False


def refresh_jellyfin_item_by_video_id(
    base_url: str,
    api_key: str,
    video_id: str,
    media_roots: list[str] = None,
) -> bool:
    item = find_jellyfin_item(base_url, api_key, video_id, media_roots)
    if item and item.get("Id"):
        return refresh_jellyfin_item(base_url, api_key, item["Id"])

    logger.warning(f"Could not find Jellyfin item {video_id}; falling back to full library refresh.")
    return refresh_jellyfin_library(base_url, api_key)


def wait_for_real_jellyfin_item(
    base_url: str,
    api_key: str,
    video_id: str,
    timeout_seconds: int = 180,
    interval_seconds: int = 5,
    media_roots: list[str] = None,
):
    import time

    deadline = time.time() + max(1, timeout_seconds)
    interval = max(1, interval_seconds)
    while time.time() < deadline:
        item = find_jellyfin_item(base_url, api_key, video_id, media_roots)
        if item and _is_real_media_item(item):
            logger.info(f"Jellyfin real media item is ready for {video_id}: {item.get('Id')}")
            return item
        time.sleep(interval)

    logger.warning(f"Timed out waiting for Jellyfin real media item: {video_id}")
    return None

def send_jellyfin_notification(
    base_url: str,
    api_key: str,
    message: str,
    user_id: str = None,
    timeout_ms: int = 30000,
):
    """
    Send a notification to active sessions. If user_id is provided, only send to that user's sessions.
    """
    if not base_url or not api_key:
        return False
        
    headers = {"X-Emby-Token": api_key, "Content-Type": "application/json"}
    
    try:
        # 1. Get all active sessions
        sessions_url = f"{base_url}/Sessions"
        resp = requests.get(sessions_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return False
            
        sessions = resp.json()
        total_sessions = len(sessions)
        
        # 2. Filter sessions by user_id if provided. Jellyfin may expose the
        # same user id with or without UUID dashes depending on the source.
        target_user_id = _normalise_user_id(user_id)
        if user_id:
            matched_sessions = [
                s for s in sessions
                if _normalise_user_id(s.get("UserId")) == target_user_id
            ]
            if matched_sessions:
                sessions = matched_sessions
                logger.info(
                    f"Sending Jellyfin notification to matched user sessions: "
                    f"user_id={user_id}, count={len(sessions)}, total_sessions={total_sessions}"
                )
            else:
                logger.warning(
                    f"No active Jellyfin session matched user_id={user_id}; "
                    f"message not sent. total_sessions={total_sessions}, message={message!r}"
                )
                return False
        else:
            logger.warning(
                f"Jellyfin notification has no target user_id; message not sent. "
                f"total_sessions={total_sessions}, message={message!r}"
            )
            return False
            
        if not sessions:
            logger.warning(f"No active Jellyfin sessions; message not sent: {message!r}")
            return False
            
        # 3. Send message to each session
        sent = 0
        for session in sessions:
            session_id = session.get("Id")
            if session_id:
                msg_url = f"{base_url}/Sessions/{session_id}/Message"
                payload = {
                    "Header": "JavPlay",
                    "Text": message,
                    "TimeoutMs": max(10000, int(timeout_ms or 30000)),
                }
                msg_resp = requests.post(msg_url, headers=headers, json=payload, timeout=5)
                if msg_resp.status_code in (200, 204):
                    sent += 1
                    logger.info(
                        f"Sent Jellyfin notification to session={session_id}, "
                        f"user={session.get('UserName') or ''}, user_id={session.get('UserId') or ''}, "
                        f"message={message!r}"
                    )
                else:
                    logger.warning(
                        f"Failed to send Jellyfin notification to session={session_id}: "
                        f"{msg_resp.status_code} - {msg_resp.text[:200]}"
                    )
                
        if sent <= 0:
            logger.warning(
                f"Jellyfin notification was not delivered to any session. "
                f"filtered_sessions={len(sessions)}, total_sessions={total_sessions}, message={message!r}"
            )
            return False

        return True
    except Exception as e:
        logger.error(f"Failed to send Jellyfin notification: {e}")
        return False


def get_active_sessions(base_url: str, api_key: str):
    if not base_url or not api_key:
        return []

    headers = {"X-Emby-Token": api_key}
    try:
        resp = requests.get(f"{base_url}/Sessions", headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Failed to get Jellyfin sessions: {resp.status_code} - {resp.text[:200]}")
            return []
        return resp.json()
    except Exception as e:
        logger.warning(f"Failed to get Jellyfin sessions: {e}")
        return []
