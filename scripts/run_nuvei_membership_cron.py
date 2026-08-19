"""Invoca de forma segura el cron de membresías Nuvei en Render."""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    base_url = os.getenv(
        "MAYU_BACKEND_URL", "https://mayu-wellness-backend-v1.onrender.com"
    ).rstrip("/")
    secret = os.getenv("NUVEI_CRON_SECRET")
    if not secret:
        raise RuntimeError("Falta NUVEI_CRON_SECRET")

    request = Request(
        f"{base_url}/payments/nuvei/membership/cron/run?limit=500",
        method="POST",
        headers={"X-Cron-Secret": secret, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=120) as response:
            payload = response.read().decode("utf-8")
            print(json.dumps(json.loads(payload), ensure_ascii=False))
    except (HTTPError, URLError) as exc:
        print(f"Cron Nuvei falló: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
