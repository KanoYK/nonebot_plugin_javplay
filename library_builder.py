import os
import re
import time
import requests
from urllib.parse import quote, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from nonebot import logger

def _safe_video_id(value: str) -> str:
    match = re.search(r"[A-Za-z]{2,10}[-_ ]?\d{2,6}", value or "")
    if not match:
        return ""
    code = match.group(0).upper().replace("_", "-").replace(" ", "-")
    if "-" not in code:
        letters = re.match(r"[A-Z]+", code)
        if letters:
            code = f"{letters.group(0)}-{code[len(letters.group(0)):]}"
    return code


def _safe_child_path(base_path: str, *parts: str) -> str:
    base_abs = os.path.abspath(base_path)
    child_abs = os.path.abspath(os.path.join(base_abs, *parts))
    if child_abs != base_abs and not child_abs.startswith(base_abs + os.sep):
        raise ValueError(f"Unsafe path outside base directory: {child_abs}")
    return child_abs


def _video_strm_url(strm_url: str, video_id: str) -> str:
    if "{video_id}" in strm_url:
        return strm_url.replace("{video_id}", quote(video_id, safe=""))

    parts = urlsplit(strm_url)
    separator = "&" if parts.query else ""
    query = f"{parts.query}{separator}video_id={quote(video_id, safe='')}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def create_phantom_video(db_path: str, video_id: str, strm_url: str) -> bool:
    video_id = _safe_video_id(video_id)
    if not video_id:
        return False

    video_dir = _safe_child_path(db_path, video_id)
    strm_path = _safe_child_path(video_dir, f"{video_id}.strm")
    os.makedirs(video_dir, exist_ok=True)

    target_url = _video_strm_url(strm_url, video_id)
    if os.path.exists(strm_path):
        try:
            with open(strm_path, "r", encoding="utf-8") as f:
                old_url = f.read().strip()
            if old_url != target_url:
                with open(strm_path, "w", encoding="utf-8") as f:
                    f.write(target_url)
                logger.info(f"Updated phantom video entry URL: {strm_path}")
        except Exception as e:
            logger.warning(f"Failed to update phantom video entry {strm_path}: {e}")
        return False

    with open(strm_path, "w", encoding="utf-8") as f:
        f.write(target_url)
    logger.info(f"Created phantom video entry: {strm_path}")
    return True


def phantom_video_exists(db_path: str, video_id: str) -> bool:
    video_id = _safe_video_id(video_id)
    if not video_id:
        return False
    video_dir = _safe_child_path(db_path, video_id)
    strm_path = _safe_child_path(video_dir, f"{video_id}.strm")
    return os.path.exists(strm_path)


def remove_phantom_video(db_path: str, video_id: str) -> bool:
    video_id = _safe_video_id(video_id)
    if not video_id:
        return False

    video_dir = _safe_child_path(db_path, video_id)
    strm_path = _safe_child_path(video_dir, f"{video_id}.strm")
    if not os.path.exists(strm_path):
        return False

    os.remove(strm_path)
    try:
        if os.path.isdir(video_dir) and not os.listdir(video_dir):
            os.rmdir(video_dir)
    except OSError:
        pass
    logger.info(f"Removed phantom video entry: {strm_path}")
    return True


def _log_unmatched_html(page: int, html_content: str):
    soup = BeautifulSoup(html_content, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "no-title"
    body_text = soup.get_text(" ", strip=True)
    snippet = body_text[:500] if body_text else html_content[:500]
    logger.warning(
        f"No video items found on JavDB page {page}. "
        f"title={title!r}, snippet={snippet!r}"
    )


def build_phantom_library(
    pages: int,
    proxy: str,
    db_path: str,
    flaresolverr_url: str = None,
    strm_url: str = "http://127.0.0.1/wait.mp4",
    flaresolverr_proxy: str = None,
    start_page: int = 1,
):
    """
    Crawls JavDB latest videos and generates .strm files.
    """
    base_url = "https://javdb.com/"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"
    }
    
    if not os.path.exists(db_path):
        os.makedirs(db_path, exist_ok=True)
        
    stats = {
        "added": 0,
        "existing": 0,
        "invalid": 0,
        "pages_requested": max(0, pages),
        "pages_completed": 0,
        "start_page": max(1, int(start_page or 1)),
        "end_page": 0,
        "last_page": 0,
        "stopped_reason": "",
    }
    
    start_page = stats["start_page"]
    end_page = start_page + max(0, pages) - 1
    stats["end_page"] = end_page

    for page in range(start_page, end_page + 1):
        try:
            logger.info(f"Crawling JavDB page {page}...")
            url = f"{base_url}?page={page}"
            
            html_content = ""
            if flaresolverr_url:
                fs_url = f"{flaresolverr_url.rstrip('/')}/v1"
                payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
                if flaresolverr_proxy:
                    payload["proxy"] = {"url": flaresolverr_proxy}
                logger.info(f"Using FlareSolverr: {fs_url}")
                resp = requests.post(fs_url, json=payload, headers={"Content-Type": "application/json"}, timeout=65)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "ok":
                        html_content = data["solution"]["response"]
                    else:
                        logger.error(f"FlareSolverr failed for page {page}: {data}")
                else:
                    logger.error(f"FlareSolverr HTTP error for page {page}: {resp.status_code} {resp.text[:300]}")
            else:
                proxies = {"http": proxy, "https": proxy} if proxy else {}
                resp = requests.get(url, headers=headers, proxies=proxies, timeout=15)
                if resp.status_code == 200:
                    html_content = resp.text
                else:
                    logger.error(f"JavDB HTTP error for page {page}: {resp.status_code} {resp.text[:300]}")
                    
            if not html_content:
                logger.error(f"Failed to fetch page {page}")
                stats["stopped_reason"] = "fetch_failed"
                break
                
            soup = BeautifulSoup(html_content, 'html.parser')
            items = soup.find_all('div', class_='item')
            
            if not items:
                _log_unmatched_html(page, html_content)
                stats["stopped_reason"] = "no_items"
                break
                
            for item in items:
                code_tag = item.select_one(".video-title strong")
                if not code_tag:
                    stats["invalid"] += 1
                    continue
                    
                video_id = _safe_video_id(code_tag.text.strip())
                if not video_id:
                    stats["invalid"] += 1
                    continue
                
                if create_phantom_video(db_path, video_id, strm_url):
                    stats["added"] += 1
                else:
                    stats["existing"] += 1

            stats["pages_completed"] += 1
            stats["last_page"] = page
            
            # Anti-ban sleep (5-10 seconds between pages)
            logger.info(f"Page {page} done. Sleeping for 8 seconds to prevent IP ban...")
            time.sleep(8)
            
        except Exception as e:
            logger.error(f"Error crawling page {page}: {e}")
            stats["stopped_reason"] = "exception"
            break
            
    if not stats["stopped_reason"]:
        stats["stopped_reason"] = "completed"
    logger.info(
        f"Library build complete. Pages {start_page}-{end_page}, "
        f"added {stats['added']} new, skipped {stats['existing']} existing, "
        f"invalid {stats['invalid']} to {db_path}."
    )
    return stats
