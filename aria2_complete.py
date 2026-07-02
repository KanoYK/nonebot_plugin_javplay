import os
import sys
import requests

def main():
    if len(sys.argv) < 4:
        print("Usage: aria2_complete.py <GID> <FileNum> <FilePath>")
        sys.exit(1)
        
    gid = sys.argv[1]
    file_num = sys.argv[2]
    file_path = sys.argv[3]
    
    webhook_url = (
        os.getenv("JAVPLAY_ARIA2_WEBHOOK_URL")
        or os.getenv("javplay_aria2_webhook_url")
        or "http://127.0.0.1:14514/webhook/aria2"
    )
    webhook_token = os.getenv("JAVPLAY_WEBHOOK_TOKEN") or os.getenv("javplay_webhook_token") or ""
    payload = {
        "gid": gid,
        "file_path": file_path
    }
    
    try:
        headers = {"X-JavPlay-Token": webhook_token} if webhook_token else None
        resp = requests.post(webhook_url, json=payload, headers=headers, timeout=5)
        if resp.status_code == 200:
            print("Webhook sent successfully.")
        else:
            print(f"Webhook failed with status {resp.status_code}")
    except Exception as e:
        print(f"Failed to send webhook: {e}")

if __name__ == "__main__":
    main()
