import os
import sys
import urllib.error
import urllib.request

try:
    port = os.environ.get("PORT", "5001")
    url = os.environ.get("BASE_URL", f"http://127.0.0.1:{port}")
    with urllib.request.urlopen(f"{url.rstrip('/')}/", timeout=10) as response:
        status = response.status
    print(f"Status: {status}")
    if status == 200:
        print("HTTP OK")
        sys.exit(0)
    else:
        print("HTTP ERROR")
        sys.exit(1)
except Exception as e:
    print(f"Exception: {e}")
    sys.exit(1)
