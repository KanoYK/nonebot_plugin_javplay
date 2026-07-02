import re
import urllib.parse
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from nonebot import logger

from .config import Config


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}


def _normalise_code(value: str) -> str:
    match = re.search(r"[A-Za-z]{2,10}[-_ ]?\d{2,6}", value or "")
    if not match:
        return (value or "").strip()
    code = match.group(0).upper().replace("_", "-").replace(" ", "-")
    if "-" not in code:
        letters = re.match(r"[A-Z]+", code)
        if letters:
            code = f"{letters.group(0)}-{code[len(letters.group(0)):]}"
    return code


async def _fetch_html(url: str, config: Config) -> str:
    """Fetch HTML directly or through FlareSolverr."""
    if config.javplay_flaresolverr_url:
        fs_url = f"{config.javplay_flaresolverr_url.rstrip('/')}/v1"
        payload = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
        if config.javplay_flaresolverr_proxy:
            payload["proxy"] = {"url": config.javplay_flaresolverr_proxy}

        logger.info(f"Using FlareSolverr to fetch: {url}")
        async with httpx.AsyncClient(timeout=65.0) as client:
            resp = await client.post(
                fs_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "ok":
                return data.get("solution", {}).get("response", "")
            logger.error(f"FlareSolverr error: {data}")
            return ""

    proxy = config.javplay_proxy_http
    proxies = None
    if config.javplay_proxy_http:
        proxies = {
            "http://": config.javplay_proxy_http,
            "https://": config.javplay_proxy_http,
        }

    logger.info(f"Directly fetching: {url}")
    client_kwargs = {"timeout": 15.0, "follow_redirects": True}
    if proxy:
        client_kwargs["proxy"] = proxy

    try:
        client = httpx.AsyncClient(**client_kwargs)
    except TypeError:
        client_kwargs.pop("proxy", None)
        if proxies:
            client_kwargs["proxies"] = proxies
        client = httpx.AsyncClient(**client_kwargs)

    async with client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()
        return resp.text


def _pick_first_detail_url(base_url: str, html: str, jav_code: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    items = soup.find_all("div", class_="item")
    if not items:
        logger.warning(f"No results found on JavDB for {jav_code}")
        return None

    normalised = _normalise_code(jav_code)
    for item in items:
        text = item.get_text(" ", strip=True).upper()
        link = item.find("a", href=True)
        if link and normalised and normalised in text:
            return urllib.parse.urljoin(base_url, link["href"])

    link = items[0].find("a", href=True)
    if not link:
        return None
    return urllib.parse.urljoin(base_url, link["href"])


def _pick_best_magnet(html: str, jav_code: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    magnets = soup.find_all("a", href=lambda href: href and href.startswith("magnet:?"))
    if not magnets:
        logger.warning(f"No magnet links found on detail page for {jav_code}")
        return None

    # Prefer subtitles when available, otherwise take the largest parsed size.
    scored = []
    for index, link in enumerate(magnets):
        text = link.get_text(" ", strip=True).lower()
        size_match = re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb)", text)
        size_mb = 0.0
        if size_match:
            size_mb = float(size_match.group(1))
            if size_match.group(2) == "gb":
                size_mb *= 1024
        subtitle_bonus = 10_000 if any(word in text for word in ("字幕", "subtitle", "sub")) else 0
        scored.append((subtitle_bonus + size_mb, -index, link["href"]))

    scored.sort(reverse=True)
    return scored[0][2]


async def search_magnet(jav_code: str, config: Config) -> Optional[str]:
    """
    Search JavDB for a JAV code and return the best magnet URI.
    """
    base_url = "https://javdb.com"
    normalised_code = _normalise_code(jav_code)
    search_url = f"{base_url}/search?q={urllib.parse.quote(normalised_code)}&f=all"

    try:
        search_html = await _fetch_html(search_url, config)
        if not search_html:
            return None

        detail_url = _pick_first_detail_url(base_url, search_html, normalised_code)
        if not detail_url:
            return None

        detail_html = await _fetch_html(detail_url, config)
        if not detail_html:
            return None

        return _pick_best_magnet(detail_html, normalised_code)
    except Exception as e:
        logger.error(f"Exception during JavDB scraping for {jav_code}: {e}")
        return None
