import time
import os
import json
import posixpath
import re
from io import StringIO
from http.cookiejar import Cookie
from http.cookies import Morsel
from collections.abc import Mapping
from typing import Optional
from types import SimpleNamespace

from nonebot import logger

from .downloader import add_uri

try:
    from p115client import P115Client
    from p115client.client import rsa_decrypt, rsa_encrypt
except ImportError:
    P115Client = None
    rsa_decrypt = None
    rsa_encrypt = None
    logger.warning("p115client is not installed. 115 features will be disabled.")


PLUGIN_DIR = os.path.dirname(__file__)
COOKIE_FILE = os.path.join(PLUGIN_DIR, "115_cookie.txt")
DOWNLOAD_COOKIE_FILE = os.path.join(PLUGIN_DIR, "115_download_cookie.txt")
QRCODE_FILE = os.path.join(PLUGIN_DIR, "QCcode.jpg")
QRCODE_TIMEOUT_SECONDS = 180
QRCODE_POLL_SECONDS = 3
QRCODE_LOGIN_APP = "alipaymini"
DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DOWNLOAD_APP_CANDIDATES = ("chrome", "desktop", "web2", "web")
PROAPI_DOWNURL = "https://proapi.115.com/app/chrome/downurl"
PROAPI_HOME = "https://proapi.115.com/"
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".wmv", ".mov", ".ts", ".m2ts", ".flv", ".rmvb")
PREFERRED_VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv", ".rmvb")
DUPLICATE_TASK_ERRCODE = 10008
DEFAULT_115_JUNK_KEYWORDS = (
    "广告",
    "直播",
    "最新地址",
    "最新位址",
    "社區",
    "社区",
    "收藏不迷路",
    "防迷路",
    "防走丢",
    "网址",
    "地址",
    ".html",
    ".txt",
)


def _read_cookie_file(path: str) -> str:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception as e:
        logger.warning(f"Failed to read 115 cookie file {path}: {e}")
    return ""


def _write_cookie_file(path: str, cookie: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(cookie.strip())
        logger.info(f"115 cookie saved to plugin directory: {path}")
    except Exception as e:
        logger.warning(f"Failed to save 115 cookie file {path}: {e}")


def _read_saved_cookie() -> str:
    return _read_cookie_file(COOKIE_FILE)


def _write_saved_cookie(cookie: str) -> None:
    _write_cookie_file(COOKIE_FILE, cookie)


def _cookie_to_string(cookie_data) -> str:
    if not cookie_data:
        return ""
    if isinstance(cookie_data, str):
        return cookie_data.strip()
    if isinstance(cookie_data, Mapping):
        return "; ".join(
            f"{key}={value}"
            for key, value in cookie_data.items()
            if key and value is not None
        )
    if isinstance(cookie_data, Morsel):
        return f"{cookie_data.key}={cookie_data.value}"
    if isinstance(cookie_data, Cookie):
        return f"{cookie_data.name}={cookie_data.value}"

    try:
        parts = []
        for item in cookie_data:
            value = _cookie_to_string(item)
            if value:
                parts.append(value)
        return "; ".join(parts)
    except TypeError:
        return str(cookie_data).strip()


def _save_and_log_qrcode(qrcode_url: str) -> bool:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(qrcode_url)
        qr.make(fit=True)

        qr_ascii = StringIO()
        qr.print_ascii(out=qr_ascii, tty=False)
        logger.info("115 login QR code:\n" + qr_ascii.getvalue())

        img = qr.make_image(fill_color="black", back_color="white")
        img.save(QRCODE_FILE)
        logger.info(f"115 login QR code saved to plugin directory: {QRCODE_FILE}")
        return True
    except Exception as e:
        logger.error(f"Failed to generate 115 login QR code image: {e}")
        logger.info(f"115 login QR URL: {qrcode_url}")
        return False


def _login_115_with_qrcode() -> str:
    if not P115Client:
        return ""

    token_resp = P115Client.login_qrcode_token()
    qrcode_token = dict(token_resp.get("data") or {})
    login_uid = qrcode_token.get("uid")
    if not login_uid:
        logger.error(f"Failed to obtain 115 login QR token: {token_resp}")
        return ""

    qrcode_url = qrcode_token.pop("qrcode", "") or f"https://115.com/scan/dg-{login_uid}"
    _save_and_log_qrcode(qrcode_url)
    logger.info(f"Waiting for 115 QR login confirmation, timeout={QRCODE_TIMEOUT_SECONDS}s")

    deadline = time.time() + QRCODE_TIMEOUT_SECONDS
    last_status = None
    while time.time() < deadline:
        try:
            status_resp = P115Client.login_qrcode_scan_status(qrcode_token)
            status = (status_resp.get("data") or {}).get("status")
            if status != last_status:
                last_status = status
                if status == 0:
                    logger.info("115 QR login status: waiting for scan")
                elif status == 1:
                    logger.info("115 QR login status: scanned, waiting for confirmation")
                elif status == 2:
                    logger.info("115 QR login status: confirmed")
                    result = P115Client.login_qrcode_scan_result(login_uid, app=QRCODE_LOGIN_APP)
                    cookie = _cookie_to_string((result.get("data") or {}).get("cookie"))
                    if cookie:
                        _write_saved_cookie(cookie)
                        return cookie
                    logger.error(f"115 QR login confirmed but no cookie was returned: {result}")
                    return ""
                elif status == -1:
                    logger.error("115 QR login expired.")
                    return ""
                elif status == -2:
                    logger.error("115 QR login canceled.")
                    return ""
                else:
                    logger.warning(f"115 QR login returned unexpected status: {status_resp}")
        except Exception as e:
            logger.warning(f"Failed to check 115 QR login status: {e}")
        time.sleep(QRCODE_POLL_SECONDS)

    logger.error("115 QR login timed out.")
    return ""


def _create_115_client(config_cookie: str) -> Optional["P115Client"]:
    if not P115Client:
        logger.error("p115client not installed, cannot use 115 download.")
        return None

    cookie = _cookie_to_string(config_cookie) or _read_saved_cookie()
    if cookie:
        client = P115Client(cookie, console_qrcode=False)
        try:
            if client.login_status():
                if not config_cookie:
                    logger.info("Using saved 115 cookie from plugin directory.")
                return client
            logger.warning("Configured/saved 115 cookie is invalid or expired; QR login is required.")
        except Exception as e:
            logger.warning(f"Failed to validate 115 cookie, QR login is required: {e}")

    cookie = _login_115_with_qrcode()
    if not cookie:
        return None

    client = P115Client(cookie, console_qrcode=False)
    try:
        if not client.login_status():
            logger.error("115 QR login cookie validation failed.")
            return None
    except Exception as e:
        logger.warning(f"Failed to validate new 115 cookie: {e}")
    return client


def _client_cookie_string(client: "P115Client") -> str:
    try:
        cookie = _cookie_to_string(client.cookies)
        if cookie:
            return cookie
    except Exception:
        pass
    try:
        return _cookie_to_string(client.cookies_str)
    except Exception:
        return ""


def _load_download_client() -> Optional["P115Client"]:
    cookie = _read_cookie_file(DOWNLOAD_COOKIE_FILE)
    if not cookie:
        return None

    try:
        client = P115Client(cookie, console_qrcode=False)
        if client.login_status():
            logger.info("Using saved 115 download cookie from plugin directory.")
            return client
        logger.warning("Saved 115 download cookie is invalid or expired.")
    except Exception as e:
        logger.warning(f"Failed to validate saved 115 download cookie: {e}")
    return None


def _create_download_client(master_client: "P115Client") -> "P115Client":
    cached_client = _load_download_client()
    if cached_client:
        return cached_client

    last_error = None
    for app in ("desktop", "os_windows"):
        try:
            logger.info(f"Creating 115 download client via app={app}")
            download_client = master_client.login_another_app(app, show_warning=False)
            cookie = _client_cookie_string(download_client)
            if cookie:
                _write_cookie_file(DOWNLOAD_COOKIE_FILE, cookie)
            return download_client
        except Exception as e:
            last_error = e
            logger.warning(f"Failed to create 115 download client via app={app}: {e}")
    raise RuntimeError(f"Failed to create 115 download client: {last_error}")


def _normalise_code(value: str) -> str:
    return (value or "").lower().replace("-", "").replace("_", "").replace(" ", "")


def _split_junk_keywords(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_115_JUNK_KEYWORDS
    keywords = []
    for part in re.split(r"[,，\n\r]+", value):
        keyword = part.strip()
        if keyword:
            keywords.append(keyword)
    return tuple(keywords) or DEFAULT_115_JUNK_KEYWORDS


def _normalise_115_path(value: str) -> str:
    path = (value or "").replace("\\", "/").strip()
    while "//" in path:
        path = path.replace("//", "/")
    return path.rstrip("/")


def _video_115_savepath(base_path: str, video_id: str) -> str:
    base_path = _normalise_115_path(base_path)
    video_id = (video_id or "").strip()
    if not base_path:
        return video_id
    return posixpath.join(base_path, video_id)


def _default_temp_115_savepath(savepath: str) -> str:
    savepath = _normalise_115_path(savepath)
    parent = posixpath.dirname(savepath) if savepath else ""
    if not parent or parent == "/":
        return "/.javplay_tmp"
    return posixpath.join(parent, ".javplay_tmp")


def _offline_savepath_aliases(path: str) -> list[str]:
    path = _normalise_115_path(path)
    aliases = []
    if path:
        aliases.append(path)
        if path.startswith("/云下载/") and not path.startswith("/云下载/云下载/"):
            aliases.append(posixpath.join("/云下载", path.lstrip("/")))

    result = []
    for alias in aliases:
        if alias and alias not in result:
            result.append(alias)
    return result


def _extract_info_hash(magnet: str) -> str:
    match = re.search(r"(?:btih:|btih%3A)([A-Za-z0-9]{32,40})", magnet or "", re.I)
    return match.group(1).lower() if match else ""


def _is_video_file(name: str) -> bool:
    return os.path.splitext(name or "")[1].lower() in VIDEO_EXTS


def _is_junk_file(name: str, junk_keywords: tuple[str, ...]) -> bool:
    lower_name = (name or "").lower()
    return any(keyword.lower() in lower_name for keyword in junk_keywords)


def _item_name(item: dict) -> str:
    return item.get("n") or item.get("file_name") or item.get("name") or ""


def _item_basename(item: dict) -> str:
    return os.path.basename(_item_name(item).replace("\\", "/"))


def _item_size(item: dict) -> int:
    try:
        return int(item.get("s") or item.get("size") or item.get("file_size") or 0)
    except Exception:
        return 0


def _item_file_id(item: dict) -> str:
    for key in ("fid", "file_id", "file_id_str", "cid"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _item_pickcode(item: dict) -> str:
    return str(item.get("pc") or item.get("pick_code") or item.get("pickcode") or "")


def _item_extension(item: dict) -> str:
    ext = os.path.splitext(_item_basename(item))[1].lower()
    return ext if ext in VIDEO_EXTS else ".mp4"


def _video_extension_score(base_name: str) -> int:
    ext = os.path.splitext(base_name or "")[1].lower()
    if ext == ".mp4":
        return 80
    if ext == ".mkv":
        return 70
    if ext in PREFERRED_VIDEO_EXTS:
        return 60
    if ext in (".m2ts", ".ts"):
        return 5
    if ext in VIDEO_EXTS:
        return 20
    return 0


def _select_115_file(
    files: list,
    video_id: str,
    min_size_bytes: int = 0,
    junk_keywords: tuple[str, ...] = DEFAULT_115_JUNK_KEYWORDS,
    require_code_match: bool = False,
) -> Optional[dict]:
    if not files:
        return None

    normalised_video_id = _normalise_code(video_id)
    scored = []
    for item in files:
        name = _item_name(item)
        base_name = _item_basename(item)
        normalised_name = _normalise_code(base_name)
        size = _item_size(item)
        if not _is_video_file(base_name):
            continue
        if _is_junk_file(base_name, junk_keywords):
            continue
        if min_size_bytes and size and size < min_size_bytes:
            continue
        code_matched = bool(normalised_video_id and normalised_video_id in normalised_name)
        score = 0
        if code_matched:
            score += 100
        stem = os.path.splitext(base_name)[0]
        if normalised_video_id and _normalise_code(stem) == normalised_video_id:
            score += 80
        elif normalised_video_id and re.search(r"\(\d+\)$", stem) and normalised_name.startswith(normalised_video_id):
            score -= 30
        score += _video_extension_score(base_name)
        score += min(size // (1024 * 1024 * 1024), 20)
        scored.append((code_matched, score, size, item))

    if any(pair[0] for pair in scored):
        scored = [pair for pair in scored if pair[0]]
    elif require_code_match:
        return None

    scored.sort(key=lambda pair: (pair[1], pair[2]), reverse=True)
    if scored:
        return scored[0][3]

    if require_code_match:
        return None

    legacy_video_files = [
        item for item in files
        if _is_video_file(_item_basename(item))
        and not _is_junk_file(_item_basename(item), junk_keywords)
    ]
    if legacy_video_files:
        legacy_video_files.sort(key=_item_size, reverse=True)
        return legacy_video_files[0]
    return None


def _extract_115_file_list(resp: dict) -> list:
    if not isinstance(resp, dict):
        return []

    containers = [
        resp,
        resp.get("data"),
        resp.get("info"),
        resp.get("torrent"),
    ]
    for container in containers:
        if isinstance(container, list):
            return [item for item in container if isinstance(item, dict)]
        if not isinstance(container, dict):
            continue
        for key in ("files", "file", "file_list", "list", "items"):
            value = container.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = _extract_115_file_list(value)
                if nested:
                    return nested
    return []


def _wanted_index_for_item(files: list, selected: dict) -> str:
    for key in ("wanted", "index", "idx", "file_id", "id"):
        value = selected.get(key)
        if value not in (None, "") and str(value).isdigit():
            index = int(value)
            if 0 <= index < len(files):
                return str(index)

    for index, item in enumerate(files):
        if item is selected:
            return str(index)
        if _item_name(item) == _item_name(selected) and _item_size(item) == _item_size(selected):
            return str(index)
    return ""


def _torrent_files_from_115(client: "P115Client", info_hash: str) -> list:
    if not info_hash:
        return []

    attempts = []
    if hasattr(client, "clouddownload_torrent"):
        attempts.extend(
            [
                ("clouddownload_torrent info_hash", client.clouddownload_torrent, {"info_hash": info_hash}),
                ("clouddownload_torrent sha1", client.clouddownload_torrent, {"sha1": info_hash}),
            ]
        )
    if hasattr(client, "offline_torrent_info"):
        attempts.extend(
            [
                ("offline_torrent_info info_hash", client.offline_torrent_info, {"info_hash": info_hash}),
                ("offline_torrent_info sha1", client.offline_torrent_info, {"sha1": info_hash}),
            ]
        )

    for label, method, payload in attempts:
        try:
            resp = method(payload)
            files = _extract_115_file_list(resp)
            if files:
                logger.info(f"115 torrent file list obtained via {label}: {len(files)} files")
                return files
            logger.info(f"115 torrent file list empty via {label}: {resp}")
        except Exception as e:
            logger.info(f"115 torrent file list failed via {label}: {e}")
    return []


def _dir_id_from_response(resp: dict) -> str:
    if not isinstance(resp, dict):
        return ""
    for container in (resp, resp.get("data")):
        if not isinstance(container, dict):
            continue
        for key in ("cid", "file_id", "id", "pid"):
            value = container.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _get_115_dir_id(client: "P115Client", path: str) -> str:
    path = _normalise_115_path(path)
    if not path:
        return "0"
    try:
        resp = client.fs_dir_getid(path)
        dir_id = _dir_id_from_response(resp)
        if dir_id and (dir_id != "0" or path in ("", "/")):
            return dir_id
        logger.info(f"115 directory id not found for {path}: {resp}")
    except Exception as e:
        logger.info(f"Failed to get 115 directory id for {path}: {e}")
    return ""


def _ensure_115_dir_id(client: "P115Client", path: str) -> str:
    path = _normalise_115_path(path)
    if not path:
        return "0"

    dir_id = _get_115_dir_id(client, path)
    if dir_id:
        return dir_id

    for method_name in ("fs_makedirs_app", "fs_makedirs", "fs_mkdir_app", "fs_mkdir"):
        method = getattr(client, method_name, None)
        if not method:
            continue
        try:
            resp = method(path)
            dir_id = _dir_id_from_response(resp)
            if dir_id:
                logger.info(f"Created 115 directory via {method_name}: {path}")
                return dir_id
            dir_id = _get_115_dir_id(client, path)
            if dir_id:
                logger.info(f"Created 115 directory via {method_name}: {path}")
                return dir_id
            logger.info(f"115 directory create returned no id via {method_name}: {resp}")
        except Exception as e:
            logger.info(f"Failed to create 115 directory via {method_name} for {path}: {e}")

    return ""


def _list_115_dir_files(client: "P115Client", dir_id: str) -> list:
    if not dir_id:
        return []
    try:
        resp = client.fs_files({"cid": dir_id, "limit": 1000, "show_dir": 0, "type": 4})
        return _extract_115_file_list(resp)
    except Exception as e:
        logger.info(f"Failed to list 115 directory {dir_id}: {e}")
        return []


def _search_115_video_files(client: "P115Client", video_id: str) -> list:
    try:
        search_resp = client.fs_search({"search_value": video_id, "type": 4})
        return _extract_115_file_list(search_resp)
    except Exception as e:
        logger.info(f"[{video_id}] 115 search failed: {e}")
        return []


def _find_completed_115_file(
    client: "P115Client",
    video_id: str,
    target_savepath: str = "",
    min_size_bytes: int = 0,
    junk_keywords: tuple[str, ...] = DEFAULT_115_JUNK_KEYWORDS,
    allow_global_search: bool = True,
    require_code_match: bool = False,
) -> Optional[dict]:
    files = []
    target_dir_id = _get_115_dir_id(client, target_savepath) if target_savepath else ""
    if target_dir_id:
        files = _list_115_dir_files(client, target_dir_id)

    if not files and allow_global_search:
        files = _search_115_video_files(client, video_id)

    return _select_115_file(files, video_id, min_size_bytes, junk_keywords, require_code_match)


def _move_115_file(client: "P115Client", item: dict, target_savepath: str, video_id: str) -> bool:
    file_id = _item_file_id(item)
    if not file_id:
        logger.warning(f"[{video_id}] 115 file id missing; cannot move selected file.")
        return False

    target_dir_id = _ensure_115_dir_id(client, target_savepath)
    if not target_dir_id:
        logger.warning(f"[{video_id}] 115 target directory unavailable: {target_savepath}")
        return False

    for method_name in ("fs_move_app", "fs_move_open", "fs_move"):
        method = getattr(client, method_name, None)
        if not method:
            continue
        try:
            resp = method(file_id, pid=target_dir_id)
            if resp.get("state"):
                logger.info(f"[{video_id}] Moved selected 115 file to {target_savepath} via {method_name}")
                return True
            logger.info(f"[{video_id}] 115 move returned false via {method_name}: {resp}")
        except Exception as e:
            logger.info(f"[{video_id}] 115 move failed via {method_name}: {e}")

    return False


def _rename_115_file(client: "P115Client", item: dict, target_name: str, video_id: str) -> dict:
    current_name = _item_basename(item)
    if not target_name or current_name == target_name:
        return item

    file_id = _item_file_id(item)
    if not file_id:
        logger.info(f"[{video_id}] 115 file id missing; skip rename from {current_name} to {target_name}")
        return item

    try:
        resp = client.fs_rename_app((file_id, target_name))
        if resp.get("state"):
            logger.info(f"[{video_id}] Renamed 115 file to {target_name}")
            renamed = dict(item)
            for key in ("n", "file_name", "name"):
                if key in renamed:
                    renamed[key] = target_name
            return renamed
        logger.warning(f"[{video_id}] 115 rename failed: {resp}")
    except Exception as e:
        logger.warning(f"[{video_id}] 115 rename error: {e}")
    return item


def _is_duplicate_115_task(resp: dict) -> bool:
    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    return (
        resp.get("errcode") == DUPLICATE_TASK_ERRCODE
        or data.get("errcode") == DUPLICATE_TASK_ERRCODE
    )


def _aria2_headers_from_115(headers) -> list:
    merged = {}
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            if key and value is not None:
                merged[str(key)] = str(value)

    lower_keys = {key.lower() for key in merged}
    if "user-agent" not in lower_keys:
        merged["User-Agent"] = DOWNLOAD_USER_AGENT
    if "referer" not in lower_keys:
        merged["Referer"] = "https://115.com/"

    return [f"{key}: {value}" for key, value in merged.items()]


def _extract_acw_tc(session, response) -> str:
    for source in (getattr(response, "cookies", None), getattr(session, "cookies", None)):
        try:
            value = source.get("acw_tc")
            if value:
                return value
        except Exception:
            pass
    return ""


def _get_115_download_info_via_proapi(client: "P115Client", pickcode: str):
    if not rsa_encrypt or not rsa_decrypt:
        raise RuntimeError("p115client RSA helpers are unavailable.")

    try:
        import requests
    except ImportError as e:
        raise RuntimeError("requests is required for 115 ProAPI download URL.") from e

    cookie_header = _client_cookie_string(client)
    if not cookie_header:
        raise RuntimeError("115 client has no usable cookie for ProAPI.")

    timestamp = int(time.time())
    rand_key = f"!@###@#{timestamp}DFDR@#@#".encode("utf-8")[:16]
    payload = json.dumps({"pickcode": pickcode}, separators=(",", ":")).encode("utf-8")
    encrypted_payload = rsa_encrypt(payload, rand_key=rand_key).decode("ascii")

    session = requests.Session()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": DOWNLOAD_USER_AGENT,
        "Referer": "https://115.com/",
        "Origin": "https://115.com",
        "Cookie": cookie_header,
    }

    response = session.post(
        f"{PROAPI_DOWNURL}?t={timestamp}",
        headers=headers,
        data={"data": encrypted_payload},
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("state"):
        raise RuntimeError(result)

    decoded = rsa_decrypt(str(result.get("data", "")).encode("ascii"), rand_key=rand_key)
    decoded_json = json.loads(decoded.decode("utf-8"))
    if not isinstance(decoded_json, dict) or not decoded_json:
        raise RuntimeError("115 ProAPI returned empty decoded data.")

    file_info = next(iter(decoded_json.values()))
    url_info = file_info.get("url") or {}
    direct_url = url_info.get("url") if isinstance(url_info, Mapping) else url_info
    if not direct_url:
        raise RuntimeError("115 ProAPI returned no direct URL.")

    try:
        session.get(
            PROAPI_HOME,
            headers={
                "User-Agent": DOWNLOAD_USER_AGENT,
                "Referer": "https://115.com/",
                "Cookie": cookie_header,
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Failed to refresh 115 acw_tc cookie: {e}")

    aria2_headers = {
        "User-Agent": DOWNLOAD_USER_AGENT,
        "Referer": "https://115.com/",
    }
    acw_tc = _extract_acw_tc(session, response)
    if acw_tc:
        aria2_headers["Cookie"] = f"acw_tc={acw_tc}"
    else:
        logger.warning("115 ProAPI did not return acw_tc; Aria2 may fail to fetch the direct URL.")

    return SimpleNamespace(
        url=direct_url,
        headers=aria2_headers,
        name=file_info.get("file_name") or "",
        size=file_info.get("file_size") or 0,
        pickcode=file_info.get("pick_code") or pickcode,
    )


def _get_115_download_info(client: "P115Client", pickcode: str):
    last_error = None
    clients = [client]
    try:
        clients.insert(0, _create_download_client(client))
    except Exception as e:
        logger.warning(f"115 download client is unavailable, falling back to current client: {e}")

    seen_clients = set()
    for current_client in clients:
        client_key = id(current_client)
        if client_key in seen_clients:
            continue
        seen_clients.add(client_key)

        try:
            current_app = current_client.login_app()
        except Exception:
            current_app = "unknown"
        logger.info(f"Trying 115 download URLs with client app={current_app or 'unknown'}")

        try:
            logger.info("Trying 115 download URL via ProAPI chrome downurl")
            dl_info = _get_115_download_info_via_proapi(current_client, pickcode)
            if getattr(dl_info, "url", ""):
                logger.info("Obtained 115 direct URL via ProAPI chrome downurl")
                return dl_info
            last_error = "empty URL from ProAPI chrome downurl"
            logger.warning("115 ProAPI returned empty download URL")
        except Exception as e:
            last_error = e
            logger.warning(f"Failed to obtain 115 direct URL via ProAPI chrome downurl: {e}")

        for app in DOWNLOAD_APP_CANDIDATES:
            try:
                logger.info(f"Trying 115 download URL via app={app}")
                dl_info = current_client.download_url(
                    pickcode,
                    app=app,
                    user_agent=DOWNLOAD_USER_AGENT,
                )
                if getattr(dl_info, "url", ""):
                    logger.info(f"Obtained 115 direct URL via app={app}")
                    return dl_info
                last_error = f"empty URL from app={app}"
                logger.warning(f"115 returned empty download URL via app={app}")
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to obtain 115 direct URL via app={app}: {e}")

    if os.path.exists(DOWNLOAD_COOKIE_FILE):
        try:
            os.remove(DOWNLOAD_COOKIE_FILE)
            logger.warning("Removed invalid 115 download cookie; it will be recreated next time.")
        except Exception as e:
            logger.warning(f"Failed to remove invalid 115 download cookie: {e}")
    raise RuntimeError(f"Failed to obtain 115 direct URL from all apps: {last_error}")


def download_via_115(
    cookie: str,
    magnet: str,
    video_id: str,
    aria2_rpc: str,
    aria2_secret: str,
    aria2_dir: str,
    savepath: str = "",
) -> Optional[str]:
    """
    Submit a magnet to 115 offline download, wait for the file, then push its URL to Aria2.

    Returns the Aria2 GID on success, otherwise None.
    """
    if not P115Client:
        logger.error("p115client not installed, cannot use 115 download.")
        return None

    try:
        client = _create_115_client(cookie)
        if not client:
            logger.error("115 login is unavailable; cannot use 115 download.")
            return None

        resp = client.clouddownload_task_add_url({"url": magnet, "savepath": savepath})
        if not resp.get("state"):
            if _is_duplicate_115_task(resp):
                task_hash = (resp.get("data") or {}).get("info_hash") or resp.get("info_hash")
                logger.info(
                    f"[{video_id}] 115 offline task already exists, hash: {task_hash}. "
                    "Searching existing offline files..."
                )
            else:
                logger.error(f"[{video_id}] 115 failed to add magnet task: {resp}")
                return None
        else:
            task_hash = resp.get("info_hash")
            logger.info(f"[{video_id}] 115 offline task added successfully, hash: {task_hash}. Waiting for completion...")

        max_retries = 30
        found_file = None

        for attempt in range(max_retries):
            time.sleep(5)

            data = _search_115_video_files(client, video_id)
            if data:
                found_file = _select_115_file(data, video_id)
                if found_file:
                    logger.info(f"[{video_id}] Found completed 115 file: {_item_name(found_file) or 'unknown'}")
                    break
                logger.info(f"[{video_id}] 115 search returned files, but no clean video candidate was selected.")
                break

            logger.info(f"[{video_id}] 115 offline task not completed yet (attempt {attempt + 1}/{max_retries})")

        if not found_file:
            logger.error(f"[{video_id}] 115 offline task timed out or file not found.")
            return None

        pickcode = _item_pickcode(found_file)
        if not pickcode:
            logger.error(f"[{video_id}] Could not find pickcode in the 115 search results.")
            return None

        dl_info = _get_115_download_info(client, pickcode)
        aria2_headers = _aria2_headers_from_115(getattr(dl_info, "headers", {}))
        download_dir = posixpath.join(aria2_dir.rstrip("/"), video_id)
        out_name = f"{video_id}{_item_extension(found_file)}"

        logger.info(f"[{video_id}] Obtained 115 direct URL. Pushing to Aria2 RPC: dir={download_dir}, out={out_name}")
        return add_uri(
            magnet_link=dl_info.url,
            video_id=video_id,
            rpc_url=aria2_rpc,
            secret=aria2_secret,
            download_dir=download_dir,
            headers=aria2_headers,
            out_name=out_name,
        )

    except Exception as e:
        logger.error(f"Error in 115 download flow: {e}")
        return None


def download_to_115_mount(
    cookie: str,
    magnet: str,
    video_id: str,
    savepath: str,
    min_video_size_mb: int = 300,
    junk_keywords: str = "",
    require_wanted_selection: bool = True,
    temp_savepath: str = "",
) -> Optional[dict]:
    """
    Submit a magnet to 115 and keep the selected video in the mounted 115 media path.

    Returns metadata for the selected 115 file on success. This function does not
    obtain a direct URL and does not push anything to Aria2.
    """
    if not P115Client:
        logger.error("p115client not installed, cannot use 115 download.")
        return None

    try:
        client = _create_115_client(cookie)
        if not client:
            logger.error("115 login is unavailable; cannot use 115 mount mode.")
            return None

        info_hash = _extract_info_hash(magnet)
        target_savepath = _video_115_savepath(savepath, video_id)
        fallback_temp_base = _normalise_115_path(temp_savepath) or _default_temp_115_savepath(savepath)
        temp_target_savepath = _video_115_savepath(fallback_temp_base, video_id)
        min_size = max(0, int(min_video_size_mb or 0)) * 1024 * 1024
        junk_list = _split_junk_keywords(junk_keywords)
        task_hash = info_hash
        selected_file = None
        wanted = ""
        used_isolated_fallback = False

        existing_target_file = _find_completed_115_file(
            client,
            video_id,
            target_savepath,
            min_size,
            junk_list,
            allow_global_search=False,
        )
        if existing_target_file:
            file_name = _item_basename(existing_target_file)
            cloud_path = posixpath.join(target_savepath, file_name)
            logger.info(
                f"[{video_id}] Reusing existing mounted 115 media file: "
                f"{file_name}, path={cloud_path}"
            )
            return {
                "mode": "115_mount",
                "info_hash": task_hash,
                "cloud_path": cloud_path,
                "savepath": target_savepath,
                "file_name": file_name,
                "size": _item_size(existing_target_file),
                "pickcode": _item_pickcode(existing_target_file),
            }

        if info_hash:
            torrent_files = _torrent_files_from_115(client, info_hash)
            selected_file = _select_115_file(torrent_files, video_id, min_size, junk_list)
            if selected_file:
                wanted = _wanted_index_for_item(torrent_files, selected_file)
                logger.info(
                    f"[{video_id}] 115 selected torrent file: "
                    f"wanted={wanted}, name={_item_name(selected_file)}, size={_item_size(selected_file)}"
                )

        if selected_file and wanted:
            payload = {
                "info_hash": info_hash,
                "wanted": wanted,
                "savepath": target_savepath,
            }
            resp = client.clouddownload_task_add_bt(payload)
            if not resp.get("state"):
                if _is_duplicate_115_task(resp):
                    task_hash = (resp.get("data") or {}).get("info_hash") or resp.get("info_hash") or task_hash
                    files = _extract_115_file_list(resp)
                    duplicate_file = _select_115_file(files, video_id, min_size, junk_list)
                    if duplicate_file:
                        selected_file = duplicate_file
                    logger.info(
                        f"[{video_id}] 115 selected offline task already exists, hash: {task_hash}. "
                        "Waiting for mounted media file..."
                    )
                else:
                    logger.error(f"[{video_id}] 115 failed to add selected BT task: {resp}")
                    return None
            else:
                task_hash = resp.get("info_hash") or task_hash
                logger.info(
                    f"[{video_id}] 115 selected BT task added successfully, hash: {task_hash}. "
                    "Waiting for mounted media file..."
                )
        else:
            if require_wanted_selection:
                logger.warning(
                    f"[{video_id}] Torrent file list or wanted index is unavailable. "
                    f"Falling back to isolated 115 path: {temp_target_savepath}"
                )
                add_savepath = temp_target_savepath
                used_isolated_fallback = True
                if not _ensure_115_dir_id(client, add_savepath):
                    logger.error(f"[{video_id}] Cannot create isolated 115 temp path: {add_savepath}")
                    return None
            else:
                logger.warning(
                    f"[{video_id}] Adding 115 task without wanted selection. "
                    "This may keep unwanted files in the 115 folder."
                )
                add_savepath = target_savepath

            resp = client.clouddownload_task_add_url({"url": magnet, "savepath": add_savepath})
            if not resp.get("state"):
                if _is_duplicate_115_task(resp):
                    task_hash = (resp.get("data") or {}).get("info_hash") or resp.get("info_hash") or task_hash
                    files = _extract_115_file_list(resp)
                    selected_file = _select_115_file(files, video_id, min_size, junk_list) or selected_file
                    logger.info(
                        f"[{video_id}] 115 offline task already exists, hash: {task_hash}. "
                        f"Waiting for clean media file from {'isolated fallback' if used_isolated_fallback else 'target path'}."
                    )
                else:
                    logger.error(f"[{video_id}] 115 failed to add magnet task: {resp}")
                    return None
            else:
                task_hash = resp.get("info_hash") or task_hash
                logger.info(
                    f"[{video_id}] 115 offline task added successfully, hash: {task_hash}, "
                    f"savepath={add_savepath}."
                )

        max_retries = 60
        found_file = None
        for attempt in range(max_retries):
            time.sleep(5)
            lookup_paths = (
                _offline_savepath_aliases(temp_target_savepath)
                if used_isolated_fallback
                else [target_savepath]
            )
            for lookup_path in lookup_paths:
                found_file = _find_completed_115_file(
                    client,
                    video_id,
                    lookup_path,
                    min_size,
                    junk_list,
                    allow_global_search=False,
                    require_code_match=used_isolated_fallback,
                )
                if found_file:
                    logger.info(
                        f"[{video_id}] Found completed 115 file in {lookup_path}: "
                        f"{_item_name(found_file) or 'unknown'}"
                    )
                    break
            if not found_file and used_isolated_fallback:
                found_file = _find_completed_115_file(
                    client,
                    video_id,
                    "",
                    min_size,
                    junk_list,
                    allow_global_search=True,
                    require_code_match=True,
                )
                if found_file:
                    logger.info(
                        f"[{video_id}] Found completed 115 file outside isolated path by exact code search: "
                        f"{_item_name(found_file) or 'unknown'}"
                    )
            if found_file:
                logger.info(f"[{video_id}] Found completed mounted 115 file: {_item_name(found_file) or 'unknown'}")
                break
            logger.info(f"[{video_id}] 115 mounted media file not ready yet (attempt {attempt + 1}/{max_retries})")

        if not found_file:
            logger.error(f"[{video_id}] 115 mount task timed out or clean video file not found.")
            return None

        target_name = f"{video_id}{_item_extension(found_file)}"
        found_file = _rename_115_file(client, found_file, target_name, video_id)
        if used_isolated_fallback and not _move_115_file(client, found_file, target_savepath, video_id):
            logger.error(f"[{video_id}] Failed to move selected 115 file from isolated fallback to target path.")
            return None
        file_name = _item_basename(found_file) or target_name
        cloud_path = posixpath.join(target_savepath, file_name)
        return {
            "mode": "115_mount",
            "info_hash": task_hash,
            "cloud_path": cloud_path,
            "savepath": target_savepath,
            "file_name": file_name,
            "size": _item_size(found_file),
            "pickcode": _item_pickcode(found_file),
        }

    except Exception as e:
        logger.error(f"Error in 115 mount download flow: {e}")
        return None
