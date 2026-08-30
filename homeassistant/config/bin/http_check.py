#!/usr/bin/env python3
"""
Проверка доступности HTTP-сервиса для command_line-сенсоров Home Assistant.

Зачем отдельный скрипт, а не однострочник с curl:
  * python3 гарантированно есть в контейнере HA, curl — не обязательно;
  * скрипт ВСЕГДА завершается с кодом 0 и печатает ON или OFF, поэтому
    сенсор никогда не уходит в состояние "unavailable" из-за ошибки команды;
  * коды 401/403 (сервис жив, но требует авторизации) считаются успехом.

Использование:
    python3 /config/bin/http_check.py <URL> [доп.коды через запятую]

Примеры:
    python3 /config/bin/http_check.py http://127.0.0.1:8096/health
    python3 /config/bin/http_check.py http://127.0.0.1:9080/ 200,401,403
"""

import sys
import ssl
import urllib.request
import urllib.error

OK_CODES = {200, 201, 204, 301, 302, 401, 403}
TIMEOUT = 6


def main() -> None:
    if len(sys.argv) < 2:
        print("OFF")
        return

    url = sys.argv[1]
    codes = set(OK_CODES)
    if len(sys.argv) > 2:
        codes = {int(c) for c in sys.argv[2].split(",") if c.strip().isdigit()}

    # Самоподписанные сертификаты DSM не должны валить проверку
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    request = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": "HomeAssistant-check/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=ctx) as response:
            print("ON" if response.status in codes else "OFF")
    except urllib.error.HTTPError as err:
        # Сервис ответил — значит, он жив, даже если код «неуспешный»
        print("ON" if err.code in codes else "OFF")
    except Exception:
        # Таймаут, отказ в соединении, DNS — сервис недоступен
        print("OFF")


if __name__ == "__main__":
    main()
