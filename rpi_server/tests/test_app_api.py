import ast
import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import esp32_discovery as ed
from app_api import build_app_state, cooldowns_payload, register_app_api
from persistence import CooldownManager, PersistenceDB


def runtime(stations=None, burst=None):
    stations = stations or [{"id": i, "name": f"Lettuce {i}", "status": "clear", "detections": ["big history"]}
                            for i in range(1, 5)]
    return SimpleNamespace(
        SERVER_BUILD="test-build", MODEL_COLOR_ORDER="split", start_time=0,
        station_payloads=lambda: stations,
        pump_state=lambda: {"active": False},
        esp32_state=lambda: {"ip": "10.0.0.9", "reachable": True, "scanning": False},
        health_state=lambda: {"camera_ok": True, "camera_frame_age_s": 0.4},
        capture_state=lambda: {"running": False, "done": 0, "total": 0},
        start_burst=burst or (lambda *a: None))


def client_for(state, rt):
    app = FastAPI()
    register_app_api(app, lambda: state, rt)
    return TestClient(app)


def test_cooldowns_payload_and_system_no_longer_calls_missing_method(tmp_path):
    db = PersistenceDB(str(tmp_path / "f.db"))
    payload = cooldowns_payload({"cooldown_mgr": CooldownManager(db)})
    assert set(payload) == {"pump", "sms", "buzzer_muted"} and payload["pump"]["active"] is False
    assert cooldowns_payload({}) is None
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8-sig")
    system = next(n for n in ast.parse(source).body if getattr(n, "name", "") == "api_system")
    calls = {n.attr for n in ast.walk(system) if isinstance(n, ast.Attribute)}
    assert "status_dict" not in calls and "cooldowns_payload" in {
        n.id for n in ast.walk(system) if isinstance(n, ast.Name)}


def test_app_state_shape_threats_and_rev(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    db = PersistenceDB(str(tmp_path / "f.db"))
    tid = db.commit_scan(db.begin_scan(2, "upper"), 2, "upper", [[5, 5, 20, 20, "worm", .9]])[0]
    thumb = tmp_path / "snapshots" / tid / "latest_thumb.jpg"
    db.update_track_snapshot(tid, str(thumb), str(thumb))
    state = {"db": db, "scan_state": {"running": True, "phase": "DETECT_UPPER", "station": 2,
                                      "position": "upper", "remaining_s": 30, "server_time": 1}}
    client = client_for(state, runtime())
    body = client.get("/api/app/state").json()
    assert body["build"] == "test-build" and len(body["stations"]) == 4
    assert "detections" not in body["stations"][0]                  # no duplicated history arrays
    threats = body["stations"][1]["threats"]
    assert threats[0]["class"] == "worm" and threats[0]["thumb_path"] == f"{tid}/latest_thumb.jpg"
    assert body["recent"][0]["id"] == tid
    # Countdown ticking does not change rev; a real change does.
    rev = body["rev"]
    state["scan_state"] = dict(state["scan_state"], remaining_s=12, server_time=19)
    assert client.get("/api/app/state").json()["rev"] == rev
    state["scan_state"] = dict(state["scan_state"], position="lower", phase="DETECT_LOWER")
    assert client.get("/api/app/state").json()["rev"] != rev
    assert client.get("/api/hello").json()["app"] == "farmcam"


def test_captures_list_serve_delete_and_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CAPTURE_DIR", tmp_path)
    (tmp_path / "thumbs").mkdir()
    for name in ("field_full_20261022_101500_000001.jpg", "field_full_20261022_101600_000001.jpg"):
        (tmp_path / name).write_bytes(b"\xff\xd8full")
    (tmp_path / "field_full_20261022_101600_000001.json").write_text(json.dumps({"station": 3, "position": "lower"}))
    (tmp_path / "thumbs" / "field_full_20261022_101600_000001.jpg").write_bytes(b"\xff\xd8thumb")
    (tmp_path / "secret.jpg").write_bytes(b"secret")
    client = client_for({}, runtime())
    page = client.get("/api/captures").json()
    assert page["total"] == 2 and page["items"][0]["station"] == 3 and page["items"][0]["has_thumb"]
    assert client.get("/api/captures/field_full_20261022_101600_000001.jpg/thumb").content == b"\xff\xd8thumb"
    assert client.get("/api/captures/field_full_20261022_101500_000001.jpg/thumb").content == b"\xff\xd8full"
    for bad in ("secret.jpg", "..%2Fsecret.jpg", "field_full_x.jpg"):
        assert client.get(f"/api/captures/{bad}").status_code == 404
    assert client.delete("/api/captures/field_full_20261022_101600_000001.jpg").json() == {"ok": True}
    assert not (tmp_path / "field_full_20261022_101600_000001.json").exists()
    assert client.get("/api/captures").json()["total"] == 1


def test_burst_validation_and_conflict():
    calls = []

    def burst(*args):
        calls.append(args)
        if len(calls) > 1:
            raise RuntimeError("A burst is already running")
    client = client_for({}, runtime(burst=burst))
    assert client.post("/api/capture/burst", json={"count": 3, "interval_s": 2, "station": 1, "position": "upper"}).status_code == 200
    assert calls[0] == (3, 2.0, 1, "upper", "")
    assert client.post("/api/capture/burst", json={"count": 3}).status_code == 409
    assert client.post("/api/capture/burst", json={"count": 99}).status_code == 422
    assert client.post("/api/capture/burst", json={"position": "middle"}).status_code == 422


def test_reports_summary_and_csv(tmp_path):
    db = PersistenceDB(str(tmp_path / "f.db"))
    ids = db.commit_scan(db.begin_scan(1, "lower"), 1, "lower",
                         [[5, 5, 20, 20, "worm", .9], [50, 50, 60, 60, "whiteflies", .7]])
    db.mark_false_positive(ids[1])
    client = client_for({"db": db}, runtime())
    summary = client.get("/api/reports/summary?days=7").json()
    assert summary["total"] == 1 and summary["false_positives"] == 1
    assert len(summary["days"]) == 7 and summary["days"][-1]["classes"] == {"worm": 1}
    assert summary["stations"][0]["classes"] == {"worm": 1}
    response = client.get("/api/reports/export.csv")
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert {r["class"] for r in rows} == {"worm", "whiteflies"} and "T" in rows[0]["first_seen"]
    assert client_for({}, runtime()).get("/api/reports/summary").status_code == 503


def test_esp32_scan_finds_device_and_monitor_switches(tmp_path):
    found_at = "10.229.135.77"
    probe = lambda ip, port=80, timeout=0: ip == found_at
    assert ed.scan("10.229.135.224", probe_fn=probe) == found_at
    assert ed.candidate_hosts("10.229.135.224")[0] in ("10.229.135.223", "10.229.135.225")
    assert ed.looks_like_farmcam_esp32({"pump": False, "sim": True, "rssi": -60})
    assert not ed.looks_like_farmcam_esp32({"status": "ok"})

    clock = [1000.0]
    moved = []
    monitor = ed.Esp32Monitor("10.229.135.161", 80, lambda: "10.229.135.224",
                              save_path=tmp_path / "esp32_ip.json", on_change=moved.append,
                              probe_fn=probe, clock=lambda: clock[0])
    monitor.check_once()                    # 1st miss: no scan yet
    assert monitor.ip == "10.229.135.161" and not moved
    monitor.check_once()                    # 2nd miss: scans and switches
    assert monitor.ip == found_at and moved == [found_at] and monitor.reachable
    assert ed.Esp32Monitor.load_saved(tmp_path / "esp32_ip.json") == found_at
    clock[0] += 100
    assert not monitor.reachable            # no answer for a while

def _power_client(tmp_path, monkeypatch, root=True, allowed=True, results=None):
    import app_api
    db = PersistenceDB(str(tmp_path / "f.db"))
    ran, pump = [], []
    rt = SimpleNamespace(DEV_MODE=False, esp32_cmd=lambda cmd, **kw: pump.append(cmd))
    replies = iter(results or [])
    monkeypatch.setattr(app_api, "POWER_DELAY_S", 0)
    monkeypatch.setattr(app_api.os, "name", "posix")
    monkeypatch.setattr(app_api, "_is_root", lambda: root)
    monkeypatch.setattr(app_api, "_power_allowed", lambda action: allowed)
    monkeypatch.setattr(app_api, "_run_power_command",
                        lambda cmd: ran.append(cmd) or next(replies, (True, "")))
    monkeypatch.setattr(app_api.threading, "Thread",
                        lambda target, args, **kw: SimpleNamespace(start=lambda: target(*args)))
    return client_for({"db": db}, rt), db, rt, ran, pump


def test_power_endpoint_stops_then_runs_the_right_command(tmp_path, monkeypatch):
    client, db, rt, ran, pump = _power_client(tmp_path, monkeypatch)
    assert client.post("/api/system/power", json={"action": "shutdown"}).json() == \
        {"ok": True, "action": "shutdown", "in_s": 0}
    assert client.post("/api/system/power", json={"action": "restart"}).status_code == 200
    assert ran == [["/usr/bin/systemctl", "poweroff"], ["/usr/bin/systemctl", "reboot"]]
    assert pump == ["PUMP_OFF"] * 2
    assert [e["title"] for e in db.events_after(0, 10)[0]] == ["FarmCam shutting down", "FarmCam restarting"]
    assert client.post("/api/system/power", json={"action": "format"}).status_code == 422
    rt.DEV_MODE = True                       # the PC dev server never powers anything off
    client.post("/api/system/power", json={"action": "shutdown"})
    assert len(ran) == 2


def test_power_as_a_normal_user_goes_through_sudo(tmp_path, monkeypatch):
    client, db, rt, ran, pump = _power_client(tmp_path, monkeypatch, root=False)
    client.post("/api/system/power", json={"action": "restart"})
    assert ran == [["sudo", "-n", "/usr/bin/systemctl", "reboot"]]


def test_power_without_permission_says_so_and_stops_nothing(tmp_path, monkeypatch):
    client, db, rt, ran, pump = _power_client(tmp_path, monkeypatch, root=False, allowed=False)
    reply = client.post("/api/system/power", json={"action": "shutdown"})
    assert reply.status_code == 503 and "no permission" in reply.json()["detail"]
    assert ran == [] and pump == [] and db.events_after(0, 10)[0] == []


def test_power_falls_back_then_reports_the_failure(tmp_path, monkeypatch):
    client, db, rt, ran, pump = _power_client(
        tmp_path, monkeypatch, results=[(False, "Access denied"), (False, "shutdown: not found")])
    client.post("/api/system/power", json={"action": "shutdown"})
    assert ran == [["/usr/bin/systemctl", "poweroff"], ["/usr/sbin/shutdown", "-h", "now"]]
    error = db.events_after(0, 10)[0][-1]
    assert error["kind"] == "error" and error["title"] == "Error: Could not shut down the Pi"
    assert "Access denied" in error["body"]
