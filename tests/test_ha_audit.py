"""Регрессии подробного аудита HA: отсутствие данных и изменение условий."""

from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

import jinja2
import pytest
import yaml
from conftest import ROOT

PACKAGES = ROOT / "homeassistant/config/packages"


class Loader(yaml.SafeLoader):
    """Сохраняет значение !env_var, не читая окружение тестового компьютера."""


Loader.add_multi_constructor("!", lambda loader, suffix, node: loader.construct_scalar(node))


def package(name):
    return yaml.load((PACKAGES / name).read_text(encoding="utf-8"), Loader=Loader)


def sensor(name, ident):
    for block in package(name)["template"]:
        for kind in ("sensor", "binary_sensor"):
            for entry in block.get(kind, []):
                if entry["unique_id"] == ident:
                    return entry
    raise AssertionError(ident)


def automation(name, ident):
    return next(a for a in package(name)["automation"] if a["id"] == ident)


def variables(name, script):
    return {key: value for step in package(name)["script"][script]["sequence"]
            for key, value in step.get("variables", {}).items()}


class States:
    def __init__(self, values, rows=()):
        self.values = values
        self.binary_sensor = [SimpleNamespace(entity_id=e, state=s, name=n) for e, s, n in rows]

    def __call__(self, entity):
        return self.values.get(entity, "unknown")


def render(source, values=None, attrs=None, rows=(), **extra):
    env = jinja2.Environment()
    env.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None
    env.globals.update(
        states=States(values or {}, rows),
        state_attr=lambda entity, attr: (attrs or {}).get((entity, attr)),
        is_state=lambda entity, state: (values or {}).get(entity) == state,
        is_number=lambda value: number(value),
    )
    return env.from_string(source).render(**extra).strip()


def number(value):
    try:
        return float(value) not in (float("inf"), float("-inf")) and float(value) == float(value)
    except (ValueError, TypeError):
        return False


@pytest.mark.parametrize("ident", ["nas_hot", "nas_cpu_busy", "nas_volume_filling", "nas_volume_critical"])
@pytest.mark.parametrize("value", ["unknown", "unavailable", "", "ошибка", "42"])
def test_threshold_availability(ident, value):
    entry = sensor("monitoring.yaml", ident)
    entity = re.search(r"sensor\.ds725_\w+", entry["state"])[0]
    assert render(entry["availability"], {entity: value}) == str(number(value))


@pytest.mark.parametrize("state,error,total", [("0", "TimeoutError", 0), ("unavailable", "", 8), ("0", "", 0)])
def test_voice_does_not_claim_success_without_docker(state, error, total):
    entry = sensor("alice.yaml", "alice_server_phrase")
    phrase = render(entry["state"], {
        "sensor.server_cpu_load": "12", "sensor.server_ram_load": "40",
        "sensor.server_temperature": "38", "sensor.docker_down": state,
    }, {("sensor.docker_down", "error"): error, ("sensor.docker_down", "total"): total,
        ("sensor.docker_down", "down_names"): []})
    assert "Всё работает" not in phrase
    assert "Нет данных" in phrase
    assert len(phrase) <= 99


def test_summary_distinguishes_missing_service_and_disk_data():
    data = variables("pachca.yaml", "pachca_report")
    rows = [("binary_sensor.svc_jellyfin", "on", "svc Jellyfin"),
            ("binary_sensor.svc_radarr", "unavailable", "svc Radarr")]
    assert "Radarr" in render(data["svc_unknown"], rows=rows)
    assert render(data["svc_running"], rows=rows) == "1"
    assert render(data["disks_ok"], {"sensor.ds725_drive_1_status": "normal",
                                    "sensor.ds725_drive_2_status": "unavailable"}) == "False"
    base = {"nas_ok": True, "docker_ok": True, "cont_down": 0, "svc_down": [], "disks": [],
            "svc_down_min": 0, "cont_down_min": 0, "vol_used": 40, "nas_temp": 38}
    for unknown, disks_ok in [(["Radarr"], True), ([], False)]:
        verdict = render(data["verdict"], svc_unknown=unknown, svc_total=6,
                         disks_ok=disks_ok, **base)
        assert "Всё в порядке" not in verdict


def test_summary_distinguishes_missing_count_from_zero():
    data = variables("pachca.yaml", "pachca_report")
    for name, attr in [("cont_running", "running"), ("cont_total", "total")]:
        assert render(data[name]) == "—"
        assert render(data[name], attrs={("sensor.docker_down", attr): 0}) == "0"


def test_docker_recovery_uses_problem_transition():
    trigger = automation("docker.yaml", "docker_container_recovered")["triggers"][0]
    assert trigger["trigger"] == "state"
    assert trigger["entity_id"] == "binary_sensor.docker_problem"
    assert trigger["from"] == "on" and trigger["to"] == "off"


@pytest.mark.parametrize("duration,expected", [(40, False), (299, False), (300, True), (900, True)])
def test_service_recovery_is_quiet_after_short_restart(duration, expected):
    entry = automation("monitoring.yaml", "mon_service_up")
    start = dt.datetime(2026, 10, 9, 9, tzinfo=dt.UTC)
    trigger = SimpleNamespace(from_state=SimpleNamespace(last_changed=start),
                              to_state=SimpleNamespace(last_changed=start + dt.timedelta(seconds=duration)))
    result = render(entry["conditions"][0]["value_template"], trigger=trigger,
                    as_timestamp=lambda value: value.timestamp())
    assert result == str(expected)


def test_both_disks_can_report_together():
    entry = automation("monitoring.yaml", "mon_disk_health")
    assert entry["mode"] in ("parallel", "queued")
    assert entry["max"] >= 2


def test_night_conditions_are_checked_again_before_pause():
    steps = automation("media_tv.yaml", "tv_night_sleep_check")["actions"]
    delay = next(i for i, step in enumerate(steps) if step.get("delay") == "00:03:00")
    pause = next(i for i, step in enumerate(steps) if step.get("action") == "media_player.media_pause")
    guards = {(s.get("entity_id"), s.get("state")) for s in steps[delay + 1:pause]
              if s.get("condition") == "state"}
    assert ("input_boolean.tv_no_auto_off", "off") in guards
    assert ("binary_sensor.tv_watching", "on") in guards


def test_idle_shutdown_rechecks_playback():
    guards = automation("media_tv.yaml", "tv_night_idle_off")["conditions"]
    assert any(s.get("entity_id") == "binary_sensor.tv_watching" and s.get("state") == "off" for s in guards)


@pytest.mark.parametrize("app,cast,jellyfin,expected", [
    ("org.jellyfin.androidtv", "idle", "unavailable", False),
    ("org.jellyfin.androidtv", "idle", "paused", True),
    ("com.google.android.youtube.tv", "unavailable", "off", False),
    ("com.google.android.youtube.tv", "paused", "unavailable", True),
    (None, "idle", "unavailable", False),
    (None, "playing", "unavailable", True),
])
def test_watching_is_unknown_when_its_active_source_is_missing(app, cast, jellyfin, expected):
    availability = sensor("media_tv.yaml", "tv_watching")["availability"]
    values = {"media_player.lg_tv": "on", "media_player.rocktek_gx1_cast": cast,
              "media_player.jellyfin_rocktek_gx1": jellyfin}
    assert render(availability, values, {("media_player.rocktek_gx1", "app_id"): app}) == str(expected)


def test_resume_refreshes_data_and_restores_position_only_for_the_right_item():
    steps = package("media_tv.yaml")["script"]["tv_jellyfin_resume"]["sequence"]
    refresh = next(i for i, step in enumerate(steps) if step.get("action") == "homeassistant.update_entity")
    snapshot = next(i for i, step in enumerate(steps) if "resume_id" in step.get("variables", {}))
    assert refresh < snapshot
    data = variables("media_tv.yaml", "tv_jellyfin_resume")
    assert render(data["resume_id"], {"sensor.jellyfin_resume": "unavailable"},
                  {("sensor.jellyfin_resume", "Id"): "старый-фильм"}) == ""
    sequence = next(step["choose"][0]["sequence"] for step in steps if "choose" in step)
    seek = next(i for i, step in enumerate(sequence) if step.get("action") == "media_player.media_seek")
    assert any("media_content_id" in step.get("wait_template", "") for step in sequence[:seek])
    assert "resume_position" in sequence[seek]["data"]["seek_position"]
    assert render(data["resume_position"], attrs={
        ("sensor.jellyfin_resume", "UserData"): {"PlaybackPositionTicks": 1250000000},
    }) == "125.0"


def test_proxy_port_is_shared_by_fast_checks_and_monitor_docker():
    compose = yaml.safe_load((ROOT / "homeassistant/compose.yaml").read_text(encoding="utf-8"))
    env = compose["services"]["homeassistant"]["environment"]
    assert any(v.startswith("DOCKER_PROXY_URL=http://127.0.0.1:${DOCKERPROXY_PORT:") for v in env)
    assert "DOCKER_PROXY_URL" in package("docker.yaml")["monitor_docker"][0]["url"]
