from __future__ import annotations

import base64
import gzip
import http.client
import json
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

from layout_studio import Layout
from layout_studio.webviewer import (
    WebViewer,
    WebViewerAssetError,
    WebViewerError,
    WebViewerTimeoutError,
)


@pytest.fixture
def bridge_asset(tmp_path: Path) -> Path:
    path = tmp_path / "index.html"
    path.write_text(
        "<!doctype html><title>test app</title>"
        "<script>/* layout-studio-python protocol 1 */</script>",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def web_viewer(canonical_layout_dict, bridge_asset):
    viewer = WebViewer(
        Layout.from_dict(canonical_layout_dict),
        standalone_path=bridge_asset,
        poll_timeout=0.02,
    )
    try:
        yield viewer
    finally:
        viewer.close()


def _request(
    viewer: WebViewer,
    suffix: str = "",
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
):
    parsed = urllib.parse.urlsplit(viewer.url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2.0)
    connection.request(
        method,
        f"{parsed.path.rstrip('/')}{suffix}",
        body=body,
        headers=headers or {},
    )
    response = connection.getresponse()
    payload = response.read()
    result = response.status, dict(response.getheaders()), payload
    connection.close()
    return result


def _post_message(
    viewer: WebViewer,
    message: object,
    *,
    generation: int | None = None,
    origin: str | None = None,
    extra_headers: dict[str, str] | None = None,
):
    payload = json.dumps(message, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "Origin": viewer.url.rstrip("/").rsplit("/", 1)[0],
    }
    if origin is not None:
        headers["Origin"] = origin
    if extra_headers:
        headers.update(extra_headers)
    return _request(
        viewer,
        "/api/events" if generation is None else f"/api/events?generation={generation}",
        method="POST",
        headers=headers,
        body=payload,
    )


def _connect_generation(viewer: WebViewer) -> int:
    origin = viewer.url.rstrip("/").rsplit("/", 1)[0]
    status, _headers, payload = _request(
        viewer,
        "/api/connect",
        method="POST",
        headers={"Origin": origin},
        body=b"",
    )
    assert status == 200
    return int(json.loads(payload)["generation"])


def _commands(viewer: WebViewer, after: int = 0) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    cursor = after
    while True:
        status, _headers, payload = _request(viewer, f"/api/commands?after={cursor}")
        assert status == 200
        commands = json.loads(payload)["commands"]
        if not commands:
            return result
        assert len(commands) == 1
        result.extend(commands)
        cursor = commands[0]["seq"]


def _bridge_message(type_: str, **fields: object) -> dict[str, object]:
    return {
        "source": "layout-studio-python",
        "protocol": 1,
        "type": type_,
        **fields,
    }


def test_wrapper_uses_capability_urls_without_embedding_layout(web_viewer):
    status, headers, body = _request(web_viewer)

    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    text = body.decode()
    assert "/api/commands" in text
    assert "/api/state" in text
    assert "/api/connect" in text
    assert "MessageChannel" in text
    assert "connectAttempts < 20" in text
    assert "window.setTimeout(() => void connect(), 500)" in text
    assert "expectedServerGeneration" in text
    assert "layout snapshot changed during restore" in text
    assert "readback invalidated by viewer reconnect" in text
    assert "viewer response exceeds the 32 MiB bridge limit" in text
    assert "pendingEventReports" in text
    assert "void connect();" in text
    assert "representedBySnapshot" in text
    assert "snapshot.fit" in text
    assert "#112233" not in text
    assert '"reference_curves"' not in text


def test_state_endpoint_reconstructs_current_controls(web_viewer):
    status, _headers, payload = _request(web_viewer, "/api/state")
    assert status == 200
    state = json.loads(payload)
    assert state == {
        "sequence": 0,
        "latestSequence": 1,
        "version": 1,
        "path": f"/{urllib.parse.urlsplit(web_viewer.url).path.strip('/')}/layout.json?v=1",
        "scope": {"kind": "layout"},
        "visibility": {},
        "mode": None,
        "view": None,
        "selection": None,
        "fit": None,
    }

    web_viewer.set_scope("object:Q1")
    web_viewer.set_visibility(curves=False, frames=False)
    web_viewer.set_mode("select")
    web_viewer.set_view("+z")
    web_viewer.select("Q1")
    web_viewer.fit("Q1")
    state = json.loads(_request(web_viewer, "/api/state")[2])
    assert state["sequence"] == 0
    assert state["latestSequence"] == 7
    assert state["scope"] == {"kind": "object", "name": "Q1"}
    assert state["visibility"] == {"curves": False, "frames": False}
    assert state["mode"] == "select"
    assert state["view"] == "+z"
    assert state["selection"] == {"kind": "object", "name": "Q1"}
    assert state["fit"] == {"kind": "object", "name": "Q1"}

    token = urllib.parse.urlsplit(web_viewer.url).path.strip("/")
    decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert len(decoded) == 16


def test_standalone_asset_is_reused_at_a_tokenized_route(web_viewer, bridge_asset):
    status, headers, body = _request(web_viewer, "/viewer/")

    assert status == 200
    assert body == bridge_asset.read_bytes()
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]

    status, headers, compressed = _request(
        web_viewer,
        "/viewer/",
        headers={"Accept-Encoding": "gzip"},
    )
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert compressed[4:8] == b"\0\0\0\0"
    assert gzip.decompress(compressed) == body

    status, headers, head_body = _request(
        web_viewer,
        "/viewer/",
        method="HEAD",
        headers={"Accept-Encoding": "gzip"},
    )
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert int(headers["Content-Length"]) == len(compressed)
    assert head_body == b""


def test_layout_endpoint_caches_compact_deterministic_gzip(web_viewer):
    status, headers, plain = _request(web_viewer, "/layout.json")
    assert status == 200
    assert headers.get("Content-Encoding") is None
    assert b"\n" not in plain

    status, headers, compressed = _request(
        web_viewer,
        "/layout.json",
        headers={"Accept-Encoding": "br, gzip"},
    )
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert compressed[4:8] == b"\0\0\0\0"
    assert gzip.decompress(compressed) == plain

    status, headers, explicit = _request(web_viewer, "/layout.json.gz?v=1")
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert explicit == compressed

    status, headers, q_zero = _request(
        web_viewer,
        "/layout.json",
        headers={"Accept-Encoding": "gzip;q=0"},
    )
    assert status == 200
    assert headers.get("Content-Encoding") is None
    assert q_zero == plain

    status, headers, head_body = _request(
        web_viewer,
        "/layout.json",
        method="HEAD",
        headers={"Accept-Encoding": "gzip"},
    )
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert int(headers["Content-Length"]) == len(compressed)
    assert head_body == b""


def test_update_serializes_once_and_enqueues_small_load_command(web_viewer):
    initial = _commands(web_viewer)
    assert len(initial) == 1
    assert initial[0]["command"] == "load_layout"
    assert initial[0]["id"] == "1"
    assert initial[0]["scope"] == {"kind": "layout"}
    assert "layout" not in initial[0]

    web_viewer.layout.curves["main"].color = "#abcdef"
    command_id = web_viewer.update()
    assert command_id == "2"

    updated = _commands(web_viewer, after=1)
    assert [command["id"] for command in updated] == ["2"]
    assert updated[0]["path"].endswith("layout.json?v=2")
    _status, _headers, payload = _request(web_viewer, "/layout.json?v=2")
    assert json.loads(payload)["reference_curves"]["main"]["color"] == "#abcdef"

    web_viewer.layout.curves["main"].color = "#fedcba"
    web_viewer.update()
    assert (
        json.loads(_request(web_viewer, "/layout.json?v=2")[2])["reference_curves"][
            "main"
        ]["color"]
        == "#abcdef"
    )
    assert (
        json.loads(_request(web_viewer, "/layout.json?v=3")[2])["reference_curves"][
            "main"
        ]["color"]
        == "#fedcba"
    )
    assert _request(web_viewer, "/layout.json?v=999")[0] == 404


def test_unapplied_layout_request_survives_state_restore_cursor(web_viewer):
    request_id = web_viewer.request_layout()

    state = json.loads(_request(web_viewer, "/api/state")[2])
    assert state["sequence"] == 0
    commands = _commands(web_viewer)
    assert [command["command"] for command in commands] == [
        "load_layout",
        "get_layout",
    ]
    assert commands[-1]["id"] == request_id
    assert json.loads(_request(web_viewer, "/api/state")[2])["sequence"] == 2


def test_scope_and_visibility_are_atomic_with_layout(
    canonical_layout_dict, bridge_asset
):
    layout = Layout.from_dict(canonical_layout_dict)
    viewer = WebViewer(
        layout,
        standalone_path=bridge_asset,
        scope=layout.curves["main"],
        visibility={"objects": False, "frames": False},
        poll_timeout=0.01,
    )
    try:
        initial = _commands(viewer)
        assert [item["command"] for item in initial] == ["load_layout"]
        assert initial[0]["scope"] == {"kind": "curve", "name": "main"}
        assert initial[0]["visibility"] == {"objects": False, "frames": False}

        assert viewer.update() == "2"
        updated = _commands(viewer, after=1)
        assert [item["command"] for item in updated] == ["load_layout"]
        assert updated[0]["scope"] == initial[0]["scope"]
        assert updated[0]["visibility"] == initial[0]["visibility"]
    finally:
        viewer.close()


def test_update_does_not_replay_imperative_view_controls(web_viewer):
    web_viewer.set_mode("pan")
    web_viewer.set_view("+z")
    web_viewer.fit("Q1")

    update_id = web_viewer.update()
    command = next(
        item for item in web_viewer._state.commands if item["id"] == update_id
    )
    assert command["command"] == "load_layout"
    assert "mode" not in command
    assert "view" not in command
    assert "fit" not in command


def test_rapid_updates_coalesce_and_keep_the_latest_snapshot(
    canonical_layout_dict, bridge_asset
):
    viewer = WebViewer(
        Layout.from_dict(canonical_layout_dict),
        standalone_path=bridge_asset,
        poll_timeout=0.001,
    )
    try:
        update_ids = [viewer.update() for _ in range(12)]
        commands = _commands(viewer)
        assert len(commands) == 1
        assert commands[0]["id"] == update_ids[-1]
        assert commands[0]["path"].endswith("layout.json?v=13")
        assert len(viewer._state.layout_versions) <= 8
        superseded = viewer.wait_response("1", timeout=0)
        assert superseded["ok"] is True
        assert superseded["result"] == {"superseded_by": "2"}
        assert json.loads(_request(viewer, "/api/state")[2])["path"].endswith(
            "layout.json?v=13"
        )
    finally:
        viewer.close()


def test_in_flight_layout_version_is_pinned_until_next_poll(
    canonical_layout_dict, bridge_asset
):
    viewer = WebViewer(
        Layout.from_dict(canonical_layout_dict),
        standalone_path=bridge_asset,
        poll_timeout=0.001,
    )
    try:
        packet = json.loads(_request(viewer, "/api/commands?after=0")[2])
        assert packet["commands"][0]["path"].endswith("layout.json?v=1")
        for _ in range(12):
            viewer.update()
        assert _request(viewer, "/layout.json?v=1")[0] == 200

        next_packet = json.loads(_request(viewer, "/api/commands?after=1")[2])
        assert next_packet["commands"][0]["path"].endswith("layout.json?v=13")
        viewer.update()
        assert _request(viewer, "/layout.json?v=1")[0] == 404
    finally:
        viewer.close()


def test_responses_advance_cursor_and_cannot_be_overwritten(web_viewer):
    command = json.loads(_request(web_viewer, "/api/commands?after=0")[2])["commands"][
        0
    ]
    real_response = _bridge_message(
        "response",
        id=command["id"],
        ok=True,
        result={"rendered": True},
    )
    assert _post_message(web_viewer, real_response)[0] == 204
    assert json.loads(_request(web_viewer, "/api/state")[2])["sequence"] == 1

    assert web_viewer.wait_response("1", timeout=0) == real_response
    assert web_viewer.update() == "2"
    with pytest.raises(WebViewerTimeoutError):
        web_viewer.wait_response("1", timeout=0)

    # A delayed duplicate from an obsolete iframe is idempotently ignored.
    assert _post_message(web_viewer, real_response)[0] == 204
    with pytest.raises(WebViewerTimeoutError):
        web_viewer.wait_response("1", timeout=0)


def test_connection_generation_rejects_stale_events(web_viewer):
    generation_1 = _connect_generation(web_viewer)
    generation_2 = _connect_generation(web_viewer)
    stale = _bridge_message(
        "event",
        event="selection",
        selection={"kind": "curve", "name": "main"},
    )
    current = _bridge_message(
        "event",
        event="selection",
        selection={"kind": "object", "name": "Q1"},
    )

    assert _post_message(web_viewer, stale, generation=generation_1)[0] == 400
    assert _post_message(web_viewer, current, generation=generation_2)[0] == 204
    state = json.loads(_request(web_viewer, f"/api/state?generation={generation_2}")[2])
    assert state["selection"] == {"kind": "object", "name": "Q1"}

    before = state["sequence"]
    status, _headers, payload = _request(
        web_viewer,
        f"/api/commands?after=999&generation={generation_1}",
    )
    assert status == 200
    assert json.loads(payload)["stale"] is True
    assert (
        json.loads(_request(web_viewer, f"/api/state?generation={generation_2}")[2])[
            "sequence"
        ]
        == before
    )


def test_state_snapshot_is_leased_until_connection_changes(web_viewer):
    generation = _connect_generation(web_viewer)
    status, _headers, payload = _request(
        web_viewer,
        f"/api/state?generation={generation}",
    )
    assert status == 200
    snapshot = json.loads(payload)
    for _ in range(12):
        web_viewer.update()

    suffix = str(snapshot["path"]).removeprefix(web_viewer._state.root_path)
    assert _request(web_viewer, suffix)[0] == 200
    assert len(web_viewer._state.layout_versions) <= 8

    assert _connect_generation(web_viewer) > generation
    web_viewer.update()
    assert _request(web_viewer, suffix)[0] == 404


def test_superseded_completion_bookkeeping_uses_a_single_range(web_viewer):
    packet = json.loads(_request(web_viewer, "/api/commands?after=0")[2])
    assert packet["commands"][0]["id"] == "1"
    last_id = "1"
    for _ in range(10_000):
        last_id = web_viewer._state.enqueue(
            "load_layout",
            layout_version=1,
            path=f"{web_viewer._state.root_path}/layout.json?v=1",
            scope={"kind": "layout"},
        )

    assert len(web_viewer._state.completed_ranges) == 1
    assert len(web_viewer._state.commands) == 2
    response = _bridge_message("response", id="1", ok=True)
    assert _post_message(web_viewer, response)[0] == 204
    assert web_viewer._state.applied_sequence == int(last_id) - 1
    assert web_viewer._state.completed_ranges == []


def test_history_cap_keeps_the_only_pending_layout_boundary(web_viewer):
    # Mark v1 applied, then publish an entity which exists only in v2.
    _request(web_viewer, "/api/commands?after=1")
    q1 = web_viewer.layout.objects["Q1"]
    q2 = web_viewer.layout.new_object(
        "Q2",
        type=web_viewer.layout.types["magnet"],
        position=q1.position.clone(),
    )
    load_id = web_viewer.update()
    scope_id = web_viewer.set_scope(q2)
    for index in range(510):
        web_viewer.set_mode("pan" if index % 2 else "orbit")

    rejected_id = web_viewer.set_mode("select")
    assert web_viewer.wait_response(rejected_id, timeout=0)["ok"] is False
    assert web_viewer._state.commands[0]["id"] == load_id
    assert web_viewer._state.commands[1]["id"] == scope_id
    packet = json.loads(_request(web_viewer, "/api/commands?after=1")[2])
    assert packet["commands"][0]["id"] == load_id
    assert packet["commands"][0]["path"].endswith("layout.json?v=2")


def test_history_cap_never_rewrites_an_inflight_response(web_viewer):
    command = json.loads(_request(web_viewer, "/api/commands?after=0")[2])["commands"][
        0
    ]
    for index in range(511):
        web_viewer.set_mode("pan" if index % 2 else "orbit")
    rejected_id = web_viewer.set_mode("select")
    assert web_viewer.wait_response(rejected_id, timeout=0)["ok"] is False

    success = _bridge_message("response", id=command["id"], ok=True)
    assert _post_message(web_viewer, success)[0] == 204
    assert web_viewer.wait_response(command["id"], timeout=0) == success


def test_eviction_expires_unserved_loads_and_ordered_readbacks(
    canonical_layout_dict, bridge_asset
):
    viewer = WebViewer(
        Layout.from_dict(canonical_layout_dict),
        standalone_path=bridge_asset,
        poll_timeout=0.001,
    )
    try:
        readback_id = viewer.request_layout()
        for index in range(12):
            viewer.set_mode("pan" if index % 2 else "orbit")
            viewer.update()

        assert len(viewer._state.layout_versions) <= 8
        queued_loads = [
            command
            for command in viewer._state.commands
            if command["command"] == "load_layout"
        ]
        assert queued_loads
        for command in queued_loads:
            version = viewer._state.layout_command_versions[command["seq"]]
            assert version in viewer._state.layout_versions
            suffix = str(command["path"]).removeprefix(viewer._state.root_path)
            assert _request(viewer, suffix)[0] == 200

        expired_load = viewer.wait_response("1", timeout=0)
        expired_readback = viewer.wait_response(readback_id, timeout=0)
        assert expired_load["ok"] is False
        assert expired_readback["ok"] is False
        assert "expired" in str(expired_readback["error"])
    finally:
        viewer.close()


def test_new_connection_supersedes_abandoned_long_polls(web_viewer):
    generation = _connect_generation(web_viewer)
    # First acknowledge the initial command, then leave a long poll waiting.
    _request(web_viewer, f"/api/commands?after=1&generation={generation}")
    started = threading.Barrier(9)
    results: list[int] = []

    def poll() -> None:
        started.wait()
        status, _headers, _payload = _request(
            web_viewer,
            f"/api/commands?after=1&generation={generation}",
        )
        results.append(status)

    threads = [threading.Thread(target=poll) for _ in range(8)]
    for thread in threads:
        thread.start()
    started.wait()
    next_generation = _connect_generation(web_viewer)
    for thread in threads:
        thread.join(timeout=0.5)

    assert next_generation > generation
    assert all(not thread.is_alive() for thread in threads)
    assert results == [200] * 8
    assert _request(web_viewer, f"/api/state?generation={next_generation}")[0] == 200


def test_public_controls_are_ordered_and_nonblocking(web_viewer):
    curve = web_viewer.layout.curves["main"]
    object_ = web_viewer.layout.objects["Q1"]
    frame = web_viewer.layout.types["magnet"].frames["survey"]

    assert web_viewer.select(curve) == "2"
    assert web_viewer.fit(object_) == "3"
    assert web_viewer.set_mode("pan") == "4"
    assert web_viewer.set_view("-z") == "5"
    assert (
        web_viewer.set_visibility(curves=False, beam_frames=True, frames=False) == "6"
    )
    assert web_viewer.select(frame) == "7"
    assert web_viewer.select("Q1->magnetic_entry") == "8"
    assert web_viewer.select(None) == "9"
    assert web_viewer.fit(web_viewer.layout) == "10"
    assert web_viewer.fit("Q1") == "11"
    assert web_viewer.set_scope("curve:main") == "12"

    commands = _commands(web_viewer, after=1)
    assert [(item["id"], item["command"]) for item in commands] == [
        ("2", "set_selection"),
        ("3", "fit"),
        ("4", "set_mode"),
        ("5", "set_view"),
        ("6", "set_visibility"),
        ("7", "set_selection"),
        ("8", "set_selection"),
        ("9", "set_selection"),
        ("10", "fit"),
        ("11", "fit"),
        ("12", "set_scope"),
    ]
    assert commands[0]["selection"] == {"kind": "curve", "name": "main"}
    assert commands[1]["target"] == {"kind": "object", "name": "Q1"}
    assert commands[3]["view"] == "-z"
    assert commands[4]["visibility"] == {
        "curves": False,
        "beam_frames": True,
        "frames": False,
    }
    assert commands[5]["selection"] == {
        "kind": "frame",
        "object": "Q1",
        "name": "survey",
    }
    assert commands[6]["selection"] == {
        "kind": "frame",
        "object": "Q1",
        "name": "magnetic_entry",
    }
    assert commands[7]["selection"] is None
    assert commands[8]["target"] == {"kind": "layout"}
    assert commands[9]["target"] == {"kind": "object", "name": "Q1"}
    assert commands[10]["scope"] == {"kind": "curve", "name": "main"}


@pytest.mark.parametrize("mode", ["zoom", "", None, 1])
def test_invalid_mode_is_rejected_without_enqueuing(web_viewer, mode):
    with pytest.raises(ValueError):
        web_viewer.set_mode(mode)
    assert len(_commands(web_viewer)) == 1


def test_invalid_visibility_and_targets_are_rejected(web_viewer):
    with pytest.raises(TypeError):
        web_viewer.set_visibility()
    with pytest.raises(TypeError):
        web_viewer.set_visibility(objects=1)
    web_viewer.set_scope("curve:main")
    with pytest.raises(ValueError, match="outside"):
        web_viewer.select("Q1")
    with pytest.raises(ValueError, match="outside"):
        web_viewer.fit("Q1")
    web_viewer.set_scope(None)
    with pytest.raises(KeyError):
        web_viewer.select("missing")
    with pytest.raises(KeyError):
        web_viewer.select({"kind": "object", "name": "missing"})
    with pytest.raises(KeyError):
        web_viewer.select({"kind": "frame", "object": "Q1", "name": "missing"})
    with pytest.raises(ValueError, match="no segment"):
        web_viewer.select({"kind": "curve", "name": "main", "segmentIndex": 999})
    with pytest.raises(ValueError):
        web_viewer.fit({"kind": "curve", "name": ""})
    with pytest.raises(KeyError):
        web_viewer.set_scope("missing")
    with pytest.raises(ValueError):
        web_viewer.set_view("top")


@pytest.mark.parametrize(
    ("method_name", "helper_name"),
    [
        ("select", "_checked_selection_target"),
        ("fit", "_fit_target"),
        ("set_scope", "_fit_target"),
    ],
)
def test_public_target_validation_holds_the_state_lock(
    web_viewer, monkeypatch, method_name, helper_name
):
    import layout_studio.webviewer as module

    original = getattr(module, helper_name)
    entered = threading.Event()
    release = threading.Event()
    failure: list[BaseException] = []

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, helper_name, blocked)

    def invoke() -> None:
        try:
            getattr(web_viewer, method_name)("Q1")
        except (AssertionError, KeyError, RuntimeError, TypeError, ValueError) as error:
            failure.append(error)

    thread = threading.Thread(target=invoke)
    thread.start()
    assert entered.wait(timeout=1)
    acquired = web_viewer._state.condition.acquire(blocking=False)
    if acquired:
        web_viewer._state.condition.release()
    release.set()
    thread.join(timeout=1)

    assert not acquired
    assert not thread.is_alive()
    assert failure == []


def test_failed_update_is_transactional_for_active_scope(web_viewer):
    web_viewer.set_scope("curve:main")
    previous_layout = web_viewer.layout
    previous_version = web_viewer._state.version
    previous_commands = list(web_viewer._state.commands)

    with pytest.raises(KeyError, match="unknown curve"):
        web_viewer.update(Layout())

    assert web_viewer.layout is previous_layout
    assert web_viewer._state.version == previous_version
    assert list(web_viewer._state.commands) == previous_commands


def test_type_and_stored_frame_selection_require_a_unique_object(web_viewer):
    type_ = web_viewer.layout.types["magnet"]
    frame = type_.frames["survey"]
    q1 = web_viewer.layout.objects["Q1"]
    web_viewer.layout.new_object("Q2", type=type_, position=q1.position.clone())

    web_viewer.set_scope(q1)
    assert web_viewer.select(type_)
    assert web_viewer.select(frame)
    scoped_commands = list(web_viewer._state.commands)[-2:]
    assert scoped_commands[0]["selection"] == {"kind": "object", "name": "Q1"}
    assert scoped_commands[1]["selection"] == {
        "kind": "frame",
        "object": "Q1",
        "name": "survey",
    }

    web_viewer.set_scope(None)
    with pytest.raises(ValueError, match="type selection is ambiguous"):
        web_viewer.select(type_)
    with pytest.raises(ValueError, match="frame selection is ambiguous"):
        web_viewer.select(frame)

    command_id = web_viewer.select("Q2->survey")
    command = _commands(web_viewer, after=int(command_id) - 1)[0]
    assert command["selection"] == {
        "kind": "frame",
        "object": "Q2",
        "name": "survey",
    }


def test_ready_selection_and_command_response_round_trip(web_viewer):
    ready = _bridge_message("event", event="ready")
    status, _headers, body = _post_message(web_viewer, ready)
    assert status == 204
    assert body == b""
    assert web_viewer.wait_ready(timeout=0) is web_viewer
    assert web_viewer.get_event() == ready

    selection = _bridge_message(
        "event",
        event="selection",
        selection={"kind": "object", "name": "Q1"},
    )
    assert _post_message(web_viewer, selection)[0] == 204
    assert web_viewer.get_event(timeout=0.1) == selection
    assert web_viewer.get_event() is None

    command_id = web_viewer.set_mode("select")
    response = _bridge_message(
        "response",
        id=command_id,
        ok=True,
        result={"mode": "select"},
    )
    assert _post_message(web_viewer, response)[0] == 204
    assert web_viewer.wait_response(command_id, timeout=0) == response

    request_id = web_viewer.request_layout()
    large_result = {"layout": {"note": "x" * (300 * 1024)}}
    readback = _bridge_message("response", id=request_id, ok=True, result=large_result)
    assert _post_message(web_viewer, readback)[0] == 204
    assert web_viewer.wait_response(request_id, timeout=0)["result"] == large_result
    assert web_viewer._state.response_bytes == 0


def test_selection_events_reject_malformed_or_unpublished_entities(web_viewer):
    valid = _bridge_message(
        "event",
        event="selection",
        selection={"kind": "object", "name": "Q1"},
    )
    malformed = _bridge_message("event", event="selection", selection="corrupt")
    null_segment = _bridge_message(
        "event",
        event="selection",
        selection={"kind": "curve", "name": "main", "segmentIndex": None},
    )
    unpublished = _bridge_message(
        "event",
        event="selection",
        selection={"kind": "object", "name": "browser-only"},
    )

    assert _post_message(web_viewer, valid)[0] == 204
    assert _post_message(web_viewer, malformed)[0] == 400
    assert _post_message(web_viewer, null_segment)[0] == 400
    assert _post_message(web_viewer, unpublished)[0] == 400
    state = json.loads(_request(web_viewer, "/api/state")[2])
    assert state["selection"] == {"kind": "object", "name": "Q1"}
    assert web_viewer.set_scope("object:Q1")


def test_selection_events_validate_the_published_not_live_layout(web_viewer):
    del web_viewer.layout.objects["Q1"]
    still_published = _bridge_message(
        "event",
        event="selection",
        selection={"kind": "object", "name": "Q1"},
    )

    assert _post_message(web_viewer, still_published)[0] == 204
    assert web_viewer._state.current_selection == {
        "kind": "object",
        "name": "Q1",
    }


def test_waits_raise_clear_timeout_errors(web_viewer):
    with pytest.raises(WebViewerTimeoutError, match="protocol 1"):
        web_viewer.wait_ready(timeout=0)
    with pytest.raises(WebViewerTimeoutError, match="no response"):
        web_viewer.wait_response("999", timeout=0)


def test_http_capability_origin_host_and_schema_checks(web_viewer):
    parsed = urllib.parse.urlsplit(web_viewer.url)
    wrong_path = parsed.path.rstrip("/") + "x/layout.json"
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    connection.request("GET", wrong_path)
    response = connection.getresponse()
    wrong_token = response.status, response.read()
    connection.close()
    assert wrong_token == (404, b"Not found\n")

    status, _headers, body = _request(
        web_viewer,
        headers={"Host": "attacker.example"},
    )
    assert (status, body) == wrong_token
    status, _headers, body = _request(
        web_viewer,
        headers={"Host": f"localhost:{parsed.port}"},
    )
    assert (status, body) == wrong_token

    status, _headers, body = _post_message(
        web_viewer,
        _bridge_message("event", event="ready"),
        origin="https://attacker.example",
    )
    assert (status, body) == wrong_token

    invalid = _bridge_message("event", event="ready", injected=True)
    assert _post_message(web_viewer, invalid)[0] == 400
    assert _post_message(web_viewer, _bridge_message("event", event=[]))[0] == 400
    assert _post_message(web_viewer, {"type": "event", "event": "ready"})[0] == 400
    assert (
        _post_message(
            web_viewer,
            _bridge_message("response", id="999", ok=True),
        )[0]
        == 400
    )
    assert _request(web_viewer, "/api/commands?after=-1")[0] == 400
    assert _request(web_viewer, f"/api/commands?after={'9' * 5000}")[0] == 400
    assert _request(web_viewer, "/api/commands?after=0&extra=1")[0] == 400
    assert _request(web_viewer, "/layout.json?callback=evil")[0] == 400
    assert _request(web_viewer, f"/layout.json?v={'9' * 5000}")[0] == 400


def test_oversized_message_is_rejected_without_waiting_for_declared_body(web_viewer):
    parsed = urllib.parse.urlsplit(web_viewer.url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
    connection.putrequest("POST", f"{parsed.path.rstrip('/')}/api/events")
    connection.putheader("Origin", web_viewer.url.rstrip("/").rsplit("/", 1)[0])
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(33 * 1024 * 1024))
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 413
    response.read()
    connection.close()


def test_hosted_url_is_explicit_and_does_not_read_an_asset(canonical_layout_dict):
    viewer = WebViewer(
        Layout.from_dict(canonical_layout_dict),
        viewer_url="https://viewer.example/app?theme=dark",
        poll_timeout=0.01,
    )
    try:
        status, headers, body = _request(viewer)
        assert status == 200
        assert "frame-src https://viewer.example" in headers["Content-Security-Policy"]
        assert headers["Permissions-Policy"].startswith("camera=()")
        assert b"https://viewer.example/app?theme=dark" in body
        assert b'sandbox="allow-downloads allow-same-origin allow-scripts"' in body
        assert _request(viewer, "/viewer/")[0] == 404
    finally:
        viewer.close()


def test_hosted_url_requires_https_except_for_literal_loopback(
    canonical_layout_dict,
):
    layout = Layout.from_dict(canonical_layout_dict)
    with pytest.raises(ValueError, match="must use HTTPS"):
        WebViewer(layout, viewer_url="http://viewer.example/app")
    with pytest.raises(ValueError, match="scoped IPv6"):
        WebViewer(layout, viewer_url="https://[fe80::1%25unsafe]/app")

    viewer = WebViewer(
        layout,
        viewer_url="http://127.0.0.1:3000/app",
        poll_timeout=0.01,
    )
    viewer.close()


def test_stale_standalone_is_rejected_before_starting_server(
    canonical_layout_dict, tmp_path
):
    stale = tmp_path / "index.html"
    stale.write_text("<!doctype html><title>old build</title>", encoding="utf-8")
    with pytest.raises(WebViewerAssetError, match="predates Python bridge protocol 1"):
        WebViewer(Layout.from_dict(canonical_layout_dict), standalone_path=stale)


def test_show_repr_context_and_close_are_clean(
    canonical_layout_dict, bridge_asset, monkeypatch
):
    opened: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(
        "layout_studio.webviewer.webbrowser.open",
        lambda url, new, autoraise: opened.append((url, new, autoraise)) or True,
    )
    started = time.monotonic()
    with WebViewer(
        Layout.from_dict(canonical_layout_dict),
        standalone_path=bridge_asset,
        poll_timeout=0.01,
        width=640,
        height=480,
    ) as viewer:
        assert viewer.show() is viewer
        assert opened == [(viewer.url, 2, True)]
        assert "height:480px;width:640px" in viewer._repr_html_()
        server_thread = viewer._thread

    assert viewer.closed
    assert not server_thread.is_alive()
    assert time.monotonic() - started < 0.4
    viewer.close()
    assert "is closed" in viewer._repr_html_()
    with pytest.raises(WebViewerError, match="closed"):
        viewer.show()
    state_before = (
        viewer._scope,
        viewer._visibility.copy(),
        viewer._state.current_selection,
        viewer._state.current_fit,
        viewer._state.current_mode,
        viewer._state.current_view,
    )
    for operation in (
        lambda: viewer.select(None),
        lambda: viewer.fit(),
        lambda: viewer.set_scope(),
        lambda: viewer.set_mode("pan"),
        lambda: viewer.set_view("+z"),
        lambda: viewer.set_visibility(curves=False),
    ):
        with pytest.raises(WebViewerError, match="closed"):
            operation()
    assert (
        viewer._scope,
        viewer._visibility,
        viewer._state.current_selection,
        viewer._state.current_fit,
        viewer._state.current_mode,
        viewer._state.current_view,
    ) == state_before


def test_close_wakes_ready_and_event_waiters(web_viewer):
    started = threading.Barrier(3)
    results: dict[str, object] = {}

    def wait_ready():
        started.wait()
        try:
            web_viewer.wait_ready(timeout=5)
        except WebViewerError as error:
            results["ready"] = error

    def wait_event():
        started.wait()
        results["event"] = web_viewer.get_event(timeout=5)

    threads = [
        threading.Thread(target=wait_ready),
        threading.Thread(target=wait_event),
    ]
    for thread in threads:
        thread.start()
    started.wait()
    web_viewer.close()
    for thread in threads:
        thread.join(timeout=0.5)

    assert all(not thread.is_alive() for thread in threads)
    assert isinstance(results["ready"], WebViewerError)
    assert results["event"] is None


def test_layout_serialization_validates_once(
    canonical_layout_dict, bridge_asset, monkeypatch
):
    layout = Layout.from_dict(canonical_layout_dict)
    original_validate = layout.validate
    calls = 0

    def counted_validate():
        nonlocal calls
        calls += 1
        return original_validate()

    monkeypatch.setattr(layout, "validate", counted_validate)
    viewer = WebViewer(layout, standalone_path=bridge_asset, poll_timeout=0.01)
    try:
        assert calls == 1
        viewer.update()
        assert calls == 2
    finally:
        viewer.close()


def test_update_preserves_selection_event_during_serialization(web_viewer, monkeypatch):
    import layout_studio.webviewer as module

    original_encode = module._encode_layout
    encoding_started = threading.Event()
    release_encoding = threading.Event()
    result: dict[str, object] = {}

    def blocked_encode(layout):
        encoding_started.set()
        assert release_encoding.wait(timeout=1)
        return original_encode(layout)

    monkeypatch.setattr(module, "_encode_layout", blocked_encode)

    def update() -> None:
        result["id"] = web_viewer.update()

    thread = threading.Thread(target=update)
    thread.start()
    assert encoding_started.wait(timeout=1)
    selection = _bridge_message(
        "event",
        event="selection",
        selection={"kind": "curve", "name": "main"},
    )
    assert _post_message(web_viewer, selection)[0] == 204
    release_encoding.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert result["id"] == "2"
    assert web_viewer._state.current_selection == {
        "kind": "curve",
        "name": "main",
    }


def test_loopback_server_thread_name_and_address(web_viewer):
    parsed = urllib.parse.urlsplit(web_viewer.url)
    assert parsed.hostname == "127.0.0.1"
    assert web_viewer._thread.daemon
    assert web_viewer._thread.name.startswith("layout-studio-web-")
    assert isinstance(web_viewer._thread, threading.Thread)


def test_argument_validation_happens_before_server_start(
    canonical_layout_dict, bridge_asset
):
    layout = Layout.from_dict(canonical_layout_dict)
    with pytest.raises(TypeError, match="mutually exclusive"):
        WebViewer(
            layout,
            standalone_path=bridge_asset,
            viewer_url="https://viewer.example",
        )
    with pytest.raises(ValueError, match="positive integer"):
        WebViewer(layout, standalone_path=bridge_asset, height=0)
    with pytest.raises(ValueError, match="poll_timeout must be positive"):
        WebViewer(layout, standalone_path=bridge_asset, poll_timeout=0)
    with pytest.raises(ValueError, match="fragment"):
        WebViewer(layout, viewer_url="https://viewer.example/#fragment")


def test_default_asset_error_explains_how_to_recover(
    monkeypatch, canonical_layout_dict
):
    import layout_studio.webviewer as module

    def missing():
        raise WebViewerAssetError(
            "no standalone web viewer was found; build webapp/build/index.html"
        )

    monkeypatch.setattr(module, "_find_standalone", missing)
    with pytest.raises(WebViewerAssetError, match="build webapp/build/index.html"):
        WebViewer(Layout.from_dict(canonical_layout_dict))
