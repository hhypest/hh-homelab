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
FORBIDDEN = [
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
ALLOWED = [
    "media/.env.example",
    "homeassistant/config/secrets.yaml.example",
    "homeassistant/config/configuration.yaml",
    "README.md",
]


def ignored(path_str: str) -> bool:
    rendered = subprocess.run(
        ["git", "check-ignore", "-q", path_str], cwd=ROOT, capture_output=True, check=False
    )
    assert rendered.returncode in (0, 1), f"git check-ignore не отработал: {rendered.stderr!r}"
    return rendered.returncode == 0


@pytest.mark.parametrize("path_str", FORBIDDEN)
def test_secrets_and_backups_are_ignored(path_str: str) -> None:
    assert ignored(path_str), f"{path_str} попадёт в коммит"


@pytest.mark.parametrize("path_str", ALLOWED)
def test_required_files_are_not_ignored(path_str: str) -> None:
    assert not ignored(path_str), f"{path_str} выпал из репозитория — правило слишком широкое"


def test_no_tracked_file_is_ignored() -> None:
    """Правило, закрывшее лишнее, тише и опаснее незакрытого."""
    rendered = subprocess.run(
        ["git", "ls-files", "-i", "-c", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    shadowed = rendered.stdout.split()
    assert not shadowed, f"эти файлы под версией, но подпадают под .gitignore: {shadowed}"
