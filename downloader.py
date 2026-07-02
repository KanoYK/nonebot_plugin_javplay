import uuid
from typing import Optional

import requests
from nonebot import logger

def add_uri(
    magnet_link: str,
    video_id: str,
    rpc_url: str,
    secret: str,
    download_dir: str,
    headers: list = None,
    out_name: str = None,
) -> Optional[str]:
    """
    Sends a magnet or HTTP URL to Aria2 RPC.

    Returns the Aria2 GID on success, otherwise None.
    """
    if not rpc_url:
        logger.error("Aria2 RPC URL not configured.")
        return None
        
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "aria2.addUri",
        "params": [
            [magnet_link],
            {
                "dir": download_dir,
            }
        ]
    }
    
    if headers:
        payload["params"][1]["header"] = headers

    if out_name:
        payload["params"][1]["out"] = out_name
    
    if secret:
        payload["params"].insert(0, f"token:{secret}")
        
    try:
        resp = requests.post(rpc_url, json=payload, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if "error" in data:
                logger.error(f"Aria2 RPC Error: {data['error']}")
                return None
            gid = data.get("result")
            logger.info(f"Successfully added {video_id} to Aria2. GID: {gid}")
            logger.info(f"Aria2 requested target for {video_id}: dir={download_dir}, out={out_name or ''}")
            status = tell_status(rpc_url, secret, gid)
            if status:
                files = status.get("files") or []
                actual_path = (files[0] or {}).get("path") if files else ""
                logger.info(
                    f"Aria2 accepted target for {video_id}: "
                    f"status={status.get('status')}, path={actual_path or 'unknown'}"
                )
            return gid
        else:
            logger.error(f"Aria2 RPC failed with status {resp.status_code}")
            return None
    except Exception as e:
        logger.error(f"Exception calling Aria2 RPC: {e}")
        return None


def tell_status(rpc_url: str, secret: str, gid: str) -> Optional[dict]:
    if not rpc_url or not gid:
        return None

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "aria2.tellStatus",
        "params": [
            gid,
            ["gid", "status", "totalLength", "completedLength", "files", "errorCode", "errorMessage"],
        ],
    }

    if secret:
        payload["params"].insert(0, f"token:{secret}")

    try:
        resp = requests.post(rpc_url, json=payload, timeout=5)
        if resp.status_code != 200:
            logger.warning(f"Aria2 tellStatus failed with status {resp.status_code}: {resp.text[:200]}")
            return None

        data = resp.json()
        if "error" in data:
            logger.warning(f"Aria2 tellStatus error for {gid}: {data['error']}")
            return None
        return data.get("result")
    except Exception as e:
        logger.warning(f"Exception calling Aria2 tellStatus for {gid}: {e}")
        return None
