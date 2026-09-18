"""
Список игнорируемого — это политика, а не перечень знакомых имён.

В .gitignore были закрыты *.key, *.pem и *.token, но не их ближайшие
соседи: wg0.conf с приватным ключом туннеля, id_rsa, выгруженный
сертификат, backup.tar с резервной копией Home Assistant. Проверено
через git check-ignore — все они попали бы в коммит открытым текстом.

Отдельная причина держать это тестом: homeassistant/config — одновременно
рабочий каталог демона и версионируемый каталог. Home Assistant сам пишет
туда ip_bans.yaml и known_devices.yaml, то есть файлы с адресами и MAC
домашней сети появляются там без участия человека.

Второй тест смотрит в обратную сторону: правило, закрывшее лишнее,
так же опасно — файл тихо выпадает из репозитория. Поэтому ни один
отслеживаемый файл не должен подпадать под игнорирование.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import ROOT

# Что не должно попасть в публичный репозиторий ни при каких обстоятельствах.
ЗАПРЕЩЕНО = [
    "homeassistant/config/secrets.yaml",
    "homeassistant/config/ip_bans.yaml",
    "homeassistant/config/known_devices.yaml",
    "wg0.conf",
    "wg-home.conf",
    "id_rsa",
    "id_ed25519",
    "server.crt",
    "client.p12",
    "keystore.jks",
    "backup.tar",
    "backup.tar.gz",
    "media/.env",
    "заметки.local.md",
    "jellyfin.token",
    "private.key",
    "fullchain.pem",
]

# Что обязано остаться под версией, несмотря на соседние правила.
РАЗРЕШЕНО = [
    "media/.env.example",
    "homeassistant/config/secrets.yaml.example",
    "homeassistant/config/configuration.yaml",
    "README.md",
]


def игнорируется(путь: str) -> bool:
    готово = subprocess.run(
        ["git", "check-ignore", "-q", путь], cwd=ROOT, capture_output=True, check=False
    )
    assert готово.returncode in (0, 1), f"git check-ignore не отработал: {готово.stderr!r}"
    return готово.returncode == 0


@pytest.mark.parametrize("путь", ЗАПРЕЩЕНО)
def test_секреты_и_копии_игнорируются(путь: str) -> None:
    assert игнорируется(путь), f"{путь} попадёт в коммит"


@pytest.mark.parametrize("путь", РАЗРЕШЕНО)
def test_нужные_файлы_не_игнорируются(путь: str) -> None:
    assert not игнорируется(путь), f"{путь} выпал из репозитория — правило слишком широкое"


def test_ни_один_отслеживаемый_файл_не_игнорируется() -> None:
    """Правило, закрывшее лишнее, тише и опаснее незакрытого."""
    готово = subprocess.run(
        ["git", "ls-files", "-i", "-c", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert готово.returncode == 0, готово.stderr
    затенённые = готово.stdout.split()
    assert not затенённые, f"эти файлы под версией, но подпадают под .gitignore: {затенённые}"
