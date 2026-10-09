"""Сериальный конвейер обязан попадать в загрузки, надзор и уведомления."""

import json

import pytest
import yaml
from conftest import ROOT
from test_ha_audit import package, render, sensor, variables
from test_pachca_templates import PACHCA
from test_pachca_templates import render as liquid_render


def test_sonarr_shares_the_download_filesystem_and_is_a_seerr_dependency():
    services = yaml.safe_load((ROOT / "media/compose.yaml").read_text(encoding="utf-8"))["services"]
    assert "sonarr" in services, "конвейер сериалов отсутствует"
    sonarr = services["sonarr"]
    assert "${MEDIA_ROOT:?см. .env.example}:/data" in sonarr["volumes"]
    assert services["radarr"]["volumes"][1] in sonarr["volumes"]
    assert sonarr["container_name"] == "sonarr"
    assert "sonarr" in services["seerr"]["depends_on"]
    assert sonarr["image"].startswith("lscr.io/linuxserver/sonarr:4.")
    assert "latest" not in sonarr["image"]


def test_sonarr_has_http_supervision_and_dashboard_metrics():
    monitoring = package("monitoring.yaml")
    checks = [row["binary_sensor"] for row in monitoring["command_line"] if "binary_sensor" in row]
    sonarr = [row for row in checks if row["unique_id"] == "svc_sonarr_http"]
    assert len(sonarr) == 1
    assert "127.0.0.1:8989/ping" in sonarr[0]["command"]
    assert "sonarr" in package("docker.yaml")["monitor_docker"][0]["containers"]
    dashboard = (ROOT / "homeassistant/dashboard-infrastructure.yaml").read_text(encoding="utf-8")
    for entity in ("binary_sensor.svc_sonarr", "sensor.docker_sonarr_cpu", "sensor.docker_sonarr_memory"):
        assert entity in dashboard


@pytest.mark.parametrize("total,healthy", [(6, False), (7, True)])
def test_report_requires_all_seven_services(total, healthy):
    verdict = variables("pachca.yaml", "pachca_report")["verdict"]
    text = render(verdict, nas_ok=True, docker_ok=True, cont_down=0, svc_down=[],
                  svc_unknown=[], svc_total=total, disks=[], disks_ok=True,
                  svc_down_min=0, cont_down_min=0, vol_used=40, nas_temp=38)
    assert ("Всё в порядке" in text) is healthy


@pytest.mark.parametrize("router", [False, True])
def test_multi_episode_notification_keeps_series_and_each_episode(router):
    payload = {"instanceName": "Домашние сериалы", "eventType": "Download",
               "series": {"title": "Пример сериала", "tvdbId": 12345},
               "episodes": [{"seasonNumber": 2, "episodeNumber": 3, "title": "Начало"},
                            {"seasonNumber": 2, "episodeNumber": 4, "title": "Продолжение"}],
               "episodeFile": {"quality": "WEBDL-1080p", "size": 1610612736}, "isUpgrade": False}
    template = PACHCA / ("media-router.liquid" if router else "sonarr.liquid")
    assert template.exists()
    text = liquid_render(template, payload)
    for token in ("Пример сериала", "S02E03", "S02E04", "1.5 ГБ"):
        assert token in text
    assert "Неопознанный" not in text


def test_sonarr_health_uses_the_root_message_and_has_a_sample():
    path = PACHCA / "samples/sonarr-health.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["eventType"] == "Health"
    for template in (PACHCA / "sonarr.liquid", PACHCA / "media-router.liquid"):
        assert payload["message"] in liquid_render(template, payload)


@pytest.mark.parametrize("sonarr_state,healthy", [("on", True), ("off", False), ("unavailable", False)])
def test_voice_includes_sonarr_in_the_health_verdict(sonarr_state, healthy):
    source = sensor("alice.yaml", "alice_server_phrase")["state"]
    rows = [("binary_sensor.svc_" + name, "on", "svc " + name)
            for name in ("qbittorrent", "prowlarr", "radarr", "flaresolverr", "jellyfin", "seerr")]
    rows.append(("binary_sensor.svc_sonarr", sonarr_state, "svc Sonarr"))
    text = render(source, {"sensor.server_cpu_load": "12", "sensor.server_ram_load": "40",
                           "sensor.server_temperature": "38", "sensor.docker_down": "0"},
                  {("sensor.docker_down", "error"): "", ("sensor.docker_down", "total"): 9,
                   ("sensor.docker_down", "down_names"): []}, rows=rows)
    assert ("Всё работает" in text) is healthy
    assert len(text.strip()) <= 99
