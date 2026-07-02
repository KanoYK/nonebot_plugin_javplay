import time
import os
import json
import posixpath
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
DUPLICATE_TASK_ERRCODE = 10008


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


def _is_video_file(name: str) -> bool:
    return os.path.splitext(name or "")[1].lower() in VIDEO_EXTS


def _item_name(item: dict) -> str:
    return item.get("n") or item.get("file_name") or item.get("name") or ""


def _item_basename(item: dict) -> str:
    return os.path.basename(_item_name(item).replace("\\", "/"))


def _item_size(item: dict) -> int:
    try:
        return int(item.get("s") or item.get("size") or item.get("file_size") or 0)
    except Exception:
        return 0


def _item_extension(item: dict) -> str:
    ext = os.path.splitext(_item_basename(item))[1].lower()
    return ext if ext in VIDEO_EXTS else ".mp4"


def _select_115_file(files: list, video_id: str) -> Optional[dict]:
    if not files:
        return None

    normalised_video_id = _normalise_code(video_id)
    scored = []
    for item in files:
        name = _item_name(item)
        base_name = _item_basename(item)
        normalised_name = _normalise_code(base_name)
        size = _item_size(item)
        score = 0
        if normalised_video_id and normalised_video_id in normalised_name:
            score += 100
        if _is_video_file(base_name):
            score += 20
        score += min(size // (1024 * 1024 * 1024), 20)
        scored.append((score, size, item))

    scored.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)
    return scored[0][2] if scored and scored[0][0] > 0 else files[0]


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

            search_resp = client.fs_search({"search_value": video_id, "type": 4})
            data = search_resp.get("data", [])
            if data:
                found_file = _select_115_file(data, video_id)
                logger.info(f"[{video_id}] Found completed 115 file: {_item_name(found_file) or 'unknown'}")
                break

            logger.info(f"[{video_id}] 115 offline task not completed yet (attempt {attempt + 1}/{max_retries})")

        if not found_file:
            logger.error(f"[{video_id}] 115 offline task timed out or file not found.")
            return None

        pickcode = found_file.get("pc")
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
