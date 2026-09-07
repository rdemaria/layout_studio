"""Non-blocking browser viewer for :mod:`layout_studio` layouts.

The viewer serves a small loopback-only bridge around the standalone web app.
Layout documents travel over tokenized HTTP endpoints instead of being embedded
in HTML or URLs.  The wrapper gives the web app a nonce-authenticated
``MessagePort`` and relays commands, responses, and events to Python.

This module intentionally uses only the Python standard library.  In a source
checkout it reuses a locally built ``webapp/build/index.html``.  Generated web
bundles are deliberately not embedded in Python distributions; installed users
can pass ``standalone_path=`` or ``viewer_url=`` explicitly.
"""

from __future__ import annotations

import gzip
import ipaddress
import json
import math
import os
import queue
import secrets
import socket
import stat as stat_module
import sys
import threading
import time
import urllib.parse
import weakref
import webbrowser
from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeAlias, cast

if sys.version_info >= (3, 11):
    from typing import Self
else:  # pragma: no cover - exercised by the supported Python 3.10 runtime
    from typing_extensions import Self

PathLike: TypeAlias = str | os.PathLike[str]
ViewerMode: TypeAlias = Literal["orbit", "pan", "select", "zoom-region"]
ViewerDirection: TypeAlias = Literal["+x", "-x", "+y", "-y", "+z", "-z"]

_BRIDGE_SOURCE = "layout-studio-python"
_BRIDGE_PROTOCOL = 1
_BRIDGE_MARKER = _BRIDGE_SOURCE.encode("ascii")
_DEFAULT_POLL_TIMEOUT = 20.0
_MAX_EVENT_BYTES = 32 * 1024 * 1024
_MAX_CATALOG_BYTES = 1024 * 1024
_MAX_CATALOG_ENTRIES = 500
_MAX_INFLIGHT_EVENT_BYTES = 32 * 1024 * 1024
_MAX_CACHED_MESSAGE_BYTES = 64 * 1024 * 1024
_MAX_COMMAND_HISTORY = 512
_MAX_LAYOUT_VERSIONS = 8
_MAX_CURSOR_DIGITS = 20
_MAX_REQUEST_HANDLERS = 32
_REQUEST_TIMEOUT = 10.0
_DEFAULT_HEIGHT = 720
class WebViewerError(RuntimeError):
    """Base class for browser-viewer failures."""


class WebViewerAssetError(WebViewerError):
    """Raised when no compatible standalone web-app build is available."""


class WebViewerTimeoutError(WebViewerError, TimeoutError):
    """Raised when the browser does not complete the bridge handshake."""


@dataclass(frozen=True)
class _StandaloneFile:
    path: Path
    content_encoding: str | None = None


@dataclass(frozen=True)
class _StandaloneAsset:
    content: bytes
    gzip_content: bytes
    label: str
    catalog_content: bytes | None
    catalog_files: Mapping[str, _StandaloneFile]


@dataclass(frozen=True)
class _SelectionCatalog:
    """Small immutable index of one serialized layout snapshot."""

    curve_segments: Mapping[str, int]
    object_frames: Mapping[str, frozenset[str]]


@dataclass
class _BridgeState:
    token: str
    nonce: str
    layout_bytes: bytes
    layout_gzip: bytes
    standalone: _StandaloneAsset | None
    viewer_url: str | None
    selection_catalog: _SelectionCatalog
    layout_versions: OrderedDict[int, tuple[bytes, bytes]] = field(
        default_factory=OrderedDict
    )
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT
    port: int = 0
    wrapper_html: bytes = b""
    version: int = 1
    next_sequence: int = 1
    applied_sequence: int = 0
    commands: deque[dict[str, object]] = field(
        default_factory=lambda: deque(maxlen=_MAX_COMMAND_HISTORY)
    )
    layout_command_versions: dict[int, int] = field(default_factory=dict)
    protected_layout_versions: dict[int, int] = field(default_factory=dict)
    restore_layout_versions: dict[int, int] = field(default_factory=dict)
    inflight_sequences: dict[int, int] = field(default_factory=dict)
    events: queue.Queue[tuple[dict[str, object], int]] = field(
        default_factory=lambda: queue.Queue(maxsize=1024)
    )
    responses: OrderedDict[str, dict[str, object]] = field(default_factory=OrderedDict)
    response_sizes: dict[str, int] = field(default_factory=dict)
    completed_ranges: list[tuple[int, int]] = field(default_factory=list)
    response_bytes: int = 0
    event_bytes: int = 0
    current_scope: dict[str, str] = field(default_factory=lambda: {"kind": "layout"})
    current_visibility: dict[str, bool] = field(default_factory=dict)
    current_mode: str | None = None
    current_view: str | None = None
    current_selection: dict[str, object] | None = None
    current_fit: dict[str, str] | None = None
    connection_generation: int = 0
    poll_ticket: int = 0
    condition: threading.Condition = field(default_factory=threading.Condition)
    ready: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def root_path(self) -> str:
        return f"/{self.token}"

    def enqueue(
        self,
        command: str,
        *,
        layout_version: int | None = None,
        **fields: object,
    ) -> str:
        with self.condition:
            if self.closed.is_set():
                raise WebViewerError("web viewer is closed")
            sequence = self.next_sequence
            self.next_sequence += 1
            command_id = str(sequence)
            item: dict[str, object] = {
                "seq": sequence,
                "source": _BRIDGE_SOURCE,
                "protocol": _BRIDGE_PROTOCOL,
                "type": "command",
                "id": command_id,
                "command": command,
            }
            item.update(fields)
            if command == "load_layout":
                while (
                    self.commands
                    and self.commands[-1]["command"] == "load_layout"
                    and cast(int, self.commands[-1]["seq"]) > self.applied_sequence
                    and not self._sequence_completed_locked(
                        cast(int, self.commands[-1]["seq"])
                    )
                    and cast(str, self.commands[-1]["id"]) not in self.responses
                    and cast(int, self.commands[-1]["seq"])
                    not in self.protected_layout_versions
                ):
                    superseded = self.commands.pop()
                    superseded_sequence = cast(int, superseded["seq"])
                    self.layout_command_versions.pop(superseded_sequence, None)
                    response = {
                        "source": _BRIDGE_SOURCE,
                        "protocol": _BRIDGE_PROTOCOL,
                        "type": "response",
                        "id": cast(str, superseded["id"]),
                        "ok": True,
                        "result": {"superseded_by": command_id},
                    }
                    self._cache_response_locked(response, len(_compact_json(response)))
                if layout_version is None:
                    raise RuntimeError(
                        "load_layout requires an internal layout version"
                    )
                self.layout_command_versions[sequence] = layout_version
            if (
                self.commands.maxlen is not None
                and len(self.commands) >= self.commands.maxlen
            ):
                head = self.commands[0]
                head_sequence = cast(int, head["seq"])
                replacement_load = next(
                    (
                        queued
                        for queued in tuple(self.commands)[1:]
                        if queued["command"] == "load_layout"
                    ),
                    None,
                )
                if head_sequence in self.inflight_sequences.values() or (
                    head["command"] == "load_layout"
                    and replacement_load is None
                    and command != "load_layout"
                ):
                    # Never rewrite the outcome of a command already handed to
                    # the viewer, nor drop the only document boundary in the
                    # queue. Reject this new command until the head advances.
                    self.layout_command_versions.pop(sequence, None)
                    response = {
                        "source": _BRIDGE_SOURCE,
                        "protocol": _BRIDGE_PROTOCOL,
                        "type": "response",
                        "id": command_id,
                        "ok": False,
                        "error": "command rejected while bridge history is full",
                    }
                    self._cache_response_locked(
                        response,
                        len(_compact_json(response)),
                    )
                    self.condition.notify_all()
                    return command_id
                expired_sequences = {head_sequence}
                if head["command"] == "load_layout":
                    # A document transaction includes every command up to the
                    # next load.  Keeping its tail after dropping the document
                    # would apply scope/selection controls to the wrong layout.
                    for queued in tuple(self.commands)[1:]:
                        if queued["command"] == "load_layout":
                            break
                        expired_sequences.add(cast(int, queued["seq"]))
                self._expire_commands_locked(
                    expired_sequences,
                    "command expired from the bounded bridge history",
                )
            self.commands.append(item)
            self.condition.notify_all()
            return command_id

    def _expire_commands_locked(self, sequences: set[int], error: str) -> None:
        if not sequences:
            return
        self.commands = deque(
            (
                command
                for command in self.commands
                if cast(int, command["seq"]) not in sequences
            ),
            maxlen=_MAX_COMMAND_HISTORY,
        )
        for sequence in sorted(sequences):
            self.layout_command_versions.pop(sequence, None)
            command_id = str(sequence)
            if (
                sequence <= self.applied_sequence
                or self._sequence_completed_locked(sequence)
                or command_id in self.responses
            ):
                continue
            response = {
                "source": _BRIDGE_SOURCE,
                "protocol": _BRIDGE_PROTOCOL,
                "type": "response",
                "id": command_id,
                "ok": False,
                "error": error,
            }
            self._cache_response_locked(
                response,
                len(_compact_json(response)),
                complete=False,
            )
            self._remember_completed_locked(sequence)
        self._advance_completed_locked(trim=False)

    def _cache_response_locked(
        self,
        message: dict[str, object],
        size: int,
        *,
        complete: bool = True,
    ) -> None:
        command_id = cast(str, message["id"])
        previous_size = self.response_sizes.pop(command_id, 0)
        if command_id in self.responses:
            del self.responses[command_id]
        self.response_bytes -= previous_size
        while self.responses and (
            len(self.responses) >= _MAX_COMMAND_HISTORY
            or self.response_bytes + size > _MAX_CACHED_MESSAGE_BYTES
        ):
            expired_id, _expired = self.responses.popitem(last=False)
            self.response_bytes -= self.response_sizes.pop(expired_id, 0)
        self.responses[command_id] = message
        self.response_sizes[command_id] = size
        self.response_bytes += size
        if complete:
            self._complete_sequence_locked(int(command_id))

    def _complete_sequence_locked(self, sequence: int) -> None:
        if sequence <= self.applied_sequence:
            return
        self._remember_completed_locked(sequence)
        self._advance_completed_locked()

    def _sequence_completed_locked(self, sequence: int) -> bool:
        if sequence <= self.applied_sequence:
            return True
        return any(start <= sequence <= end for start, end in self.completed_ranges)

    def _remember_completed_locked(self, sequence: int) -> None:
        ranges = self.completed_ranges
        for index, (start, end) in enumerate(ranges):
            if sequence < start - 1:
                ranges.insert(index, (sequence, sequence))
                return
            if sequence > end + 1:
                continue
            merged_start = min(start, sequence)
            merged_end = max(end, sequence)
            while index + 1 < len(ranges) and ranges[index + 1][0] <= merged_end + 1:
                _next_start, next_end = ranges.pop(index + 1)
                merged_end = max(merged_end, next_end)
            ranges[index] = (merged_start, merged_end)
            return
        ranges.append((sequence, sequence))

    def _advance_completed_locked(self, *, trim: bool = True) -> None:
        acknowledged = self.applied_sequence
        while self.completed_ranges and self.completed_ranges[0][0] <= acknowledged + 1:
            _start, end = self.completed_ranges.pop(0)
            acknowledged = max(acknowledged, end)
        self._advance_applied_locked(acknowledged, trim=trim)

    def _advance_applied_locked(self, acknowledged: int, *, trim: bool = True) -> None:
        if acknowledged <= self.applied_sequence:
            return
        self.applied_sequence = min(acknowledged, self.next_sequence - 1)
        self.completed_ranges = [
            (max(start, self.applied_sequence + 1), end)
            for start, end in self.completed_ranges
            if end > self.applied_sequence
        ]
        while (
            self.commands
            and cast(int, self.commands[0]["seq"]) <= self.applied_sequence
        ):
            applied = self.commands.popleft()
            self.layout_command_versions.pop(cast(int, applied["seq"]), None)
        for sequence in tuple(self.protected_layout_versions):
            if sequence <= self.applied_sequence:
                del self.protected_layout_versions[sequence]
        for generation, sequence in tuple(self.inflight_sequences.items()):
            if sequence <= self.applied_sequence:
                del self.inflight_sequences[generation]
        if trim:
            self.trim_layout_versions()

    def trim_layout_versions(self) -> None:
        protected = (
            set(self.protected_layout_versions.values())
            | set(self.restore_layout_versions.values())
            | {self.version}
        )
        removed_versions: set[int] = set()
        while len(self.layout_versions) > _MAX_LAYOUT_VERSIONS:
            expired = next(
                (
                    version
                    for version in self.layout_versions
                    if version not in protected
                ),
                None,
            )
            if expired is None:
                break
            del self.layout_versions[expired]
            removed_versions.add(expired)
        if not removed_versions:
            return

        unavailable_layout = False
        expired_sequences: set[int] = set()
        for command in self.commands:
            sequence = cast(int, command["seq"])
            if command["command"] == "load_layout":
                unavailable_layout = (
                    self.layout_command_versions.get(sequence) in removed_versions
                )
                if unavailable_layout:
                    expired_sequences.add(sequence)
            elif unavailable_layout:
                # Nothing may cross a missing document transaction.  The next
                # retained load establishes a usable document boundary, while
                # these original command ids fail honestly and remain waitable.
                expired_sequences.add(sequence)
        if not expired_sequences:
            return

        self._expire_commands_locked(
            expired_sequences,
            "command expired with its bounded layout snapshot",
        )

    def record_message(
        self,
        message: dict[str, object],
        size: int,
        *,
        generation: int,
    ) -> bool:
        with self.condition:
            if self.closed.is_set() or generation != self.connection_generation:
                return False
            message_type = message["type"]
            if message_type == "response":
                command_id = cast(str, message["id"])
                if (
                    len(command_id) > _MAX_CURSOR_DIGITS
                    or not command_id.isascii()
                    or not command_id.isdecimal()
                    or not 1 <= int(command_id) < self.next_sequence
                ):
                    return False
                sequence = int(command_id)
                if (
                    sequence <= self.applied_sequence
                    or self._sequence_completed_locked(sequence)
                    or command_id in self.responses
                ):
                    return True
                for active_generation, active_sequence in tuple(
                    self.inflight_sequences.items()
                ):
                    if active_sequence == sequence:
                        del self.inflight_sequences[active_generation]
                self._cache_response_locked(message, size)
                self.condition.notify_all()
                return True

            if message.get("event") == "ready":
                self.ready.set()
            if message.get("event") == "selection":
                selection = message.get("selection")
                if selection is None:
                    self.current_selection = None
                else:
                    if not _selection_in_catalog(
                        cast(dict[str, object], selection),
                        self.selection_catalog,
                    ):
                        return False
                    if not _selection_in_scope(selection, self.current_scope):
                        return False
                    self.current_selection = dict(cast(dict[str, object], selection))
            while not self.events.empty() and (
                self.events.full()
                or self.event_bytes + size > _MAX_CACHED_MESSAGE_BYTES
            ):
                try:
                    _expired, expired_size = self.events.get_nowait()
                except queue.Empty:  # pragma: no cover - another consumer won
                    break
                self.event_bytes -= expired_size
            try:
                self.events.put_nowait((message, size))
            except queue.Full:  # pragma: no cover - guarded above
                return True
            self.event_bytes += size
            return True

    def pop_event(self, timeout: float = 0.0) -> dict[str, object]:
        if timeout == 0.0:
            message, size = self.events.get_nowait()
        else:
            message, size = self.events.get(timeout=timeout)
        with self.condition:
            self.event_bytes = max(0, self.event_bytes - size)
        return message


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = _MAX_REQUEST_HANDLERS

    def __init__(self, state: _BridgeState) -> None:
        self.state = state
        self._handler_slots = threading.BoundedSemaphore(_MAX_REQUEST_HANDLERS)
        self._active_condition = threading.Condition()
        self._active_requests: set[socket.socket] = set()
        self._body_condition = threading.Lock()
        self._inflight_body_bytes = 0
        super().__init__(("127.0.0.1", 0), _BridgeRequestHandler)

    def process_request(
        self, request: socket.socket, client_address: tuple[str, int]
    ) -> None:
        if not self._handler_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self._active_condition:
            self._active_requests.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._active_condition:
                self._active_requests.discard(request)
                self._active_condition.notify_all()
            self._handler_slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: tuple[str, int]
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._active_condition:
                self._active_requests.discard(request)
                self._active_condition.notify_all()
            self._handler_slots.release()

    def close_active_requests(self) -> None:
        with self._active_condition:
            requests = tuple(self._active_requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                request.close()
            except OSError:
                pass

    def wait_for_handlers(self, timeout: float) -> None:
        with self._active_condition:
            self._active_condition.wait_for(
                lambda: not self._active_requests,
                timeout=timeout,
            )

    def reserve_body(self, size: int) -> bool:
        with self._body_condition:
            if self._inflight_body_bytes + size > _MAX_INFLIGHT_EVENT_BYTES:
                return False
            self._inflight_body_bytes += size
            return True

    def release_body(self, size: int) -> None:
        with self._body_condition:
            self._inflight_body_bytes = max(0, self._inflight_body_bytes - size)

    def handle_error(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
    ) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, OSError):
            # Browser/tab disconnects during a response are normal lifecycle
            # events and should not print socketserver tracebacks in notebooks.
            return
        super().handle_error(request, client_address)


class _BridgeRequestHandler(BaseHTTPRequestHandler):
    server: _BridgeServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(_REQUEST_TIMEOUT)

    def log_message(self, _format: str, *args: object) -> None:
        del args

    def do_GET(self) -> None:
        if not self._valid_host():
            self._not_found()
            return
        parsed = urllib.parse.urlsplit(self.path)
        state = self.server.state
        root = state.root_path

        if parsed.path in (root, f"{root}/") and not parsed.query:
            self._send_bytes(
                HTTPStatus.OK,
                state.wrapper_html,
                "text/html; charset=utf-8",
                wrapper=True,
            )
            return
        if parsed.path == f"{root}/viewer/" and not parsed.query:
            if state.standalone is None:
                self._not_found()
                return
            wants_gzip = _accepts_gzip(self.headers.get("Accept-Encoding", ""))
            payload = (
                state.standalone.gzip_content
                if wants_gzip
                else state.standalone.content
            )
            extra = {"Vary": "Accept-Encoding"}
            if wants_gzip:
                extra["Content-Encoding"] = "gzip"
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                "text/html; charset=utf-8",
                extra=extra,
                standalone=True,
            )
            return
        if parsed.path == f"{root}/viewer/list.json" and not parsed.query:
            if state.standalone is None or state.standalone.catalog_content is None:
                self._not_found()
                return
            self._send_bytes(
                HTTPStatus.OK,
                state.standalone.catalog_content,
                "application/json; charset=utf-8",
            )
            return
        catalog_file = self._catalog_file(parsed.path)
        if catalog_file is not None:
            self._send_file(catalog_file)
            return
        if parsed.path in (f"{root}/layout.json", f"{root}/layout.json.gz"):
            valid_query, requested_version = self._parse_layout_query(parsed.query)
            if not valid_query:
                self._bad_request()
                return
            wants_gzip = parsed.path.endswith(".gz") or _accepts_gzip(
                self.headers.get("Accept-Encoding", "")
            )
            with state.condition:
                version, snapshot = self._layout_snapshot(requested_version)
            if snapshot is None:
                self._not_found()
                return
            payload = snapshot[1] if wants_gzip else snapshot[0]
            extra = {
                "Vary": "Accept-Encoding",
                "ETag": f'W/"layout-{version}"',
                "X-Layout-Studio-Version": str(version),
            }
            if wants_gzip:
                extra["Content-Encoding"] = "gzip"
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                "application/json; charset=utf-8",
                extra=extra,
            )
            return
        if parsed.path == f"{root}/api/commands":
            command_query = self._parse_command_query(parsed.query)
            if command_query is None:
                self._bad_request()
                return
            self._serve_commands(*command_query)
            return
        if parsed.path == f"{root}/api/state" and not parsed.query:
            self._serve_state(0)
            return
        if parsed.path == f"{root}/api/state":
            generation = self._parse_generation(parsed.query)
            if generation is None:
                self._bad_request()
                return
            self._serve_state(generation)
            return
        self._not_found()

    def do_HEAD(self) -> None:
        if not self._valid_host():
            self._not_found()
            return
        parsed = urllib.parse.urlsplit(self.path)
        state = self.server.state
        root = state.root_path
        if parsed.path in (root, f"{root}/") and not parsed.query:
            self._send_bytes(
                HTTPStatus.OK,
                state.wrapper_html,
                "text/html; charset=utf-8",
                wrapper=True,
                head=True,
            )
            return
        if parsed.path == f"{root}/viewer/" and not parsed.query:
            if state.standalone is None:
                self._not_found()
                return
            wants_gzip = _accepts_gzip(self.headers.get("Accept-Encoding", ""))
            payload = (
                state.standalone.gzip_content
                if wants_gzip
                else state.standalone.content
            )
            extra = {"Vary": "Accept-Encoding"}
            if wants_gzip:
                extra["Content-Encoding"] = "gzip"
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                "text/html; charset=utf-8",
                extra=extra,
                standalone=True,
                head=True,
            )
            return
        if parsed.path == f"{root}/viewer/list.json" and not parsed.query:
            if state.standalone is None or state.standalone.catalog_content is None:
                self._not_found()
                return
            self._send_bytes(
                HTTPStatus.OK,
                state.standalone.catalog_content,
                "application/json; charset=utf-8",
                head=True,
            )
            return
        catalog_file = self._catalog_file(parsed.path)
        if catalog_file is not None:
            self._send_file(catalog_file, head=True)
            return
        if parsed.path in (f"{root}/layout.json", f"{root}/layout.json.gz"):
            valid_query, requested_version = self._parse_layout_query(parsed.query)
            if not valid_query:
                self._bad_request()
                return
            wants_gzip = parsed.path.endswith(".gz") or _accepts_gzip(
                self.headers.get("Accept-Encoding", "")
            )
            with state.condition:
                version, snapshot = self._layout_snapshot(requested_version)
            if snapshot is None:
                self._not_found()
                return
            payload = snapshot[1] if wants_gzip else snapshot[0]
            extra = {
                "Vary": "Accept-Encoding",
                "ETag": f'W/"layout-{version}"',
                "X-Layout-Studio-Version": str(version),
            }
            if wants_gzip:
                extra["Content-Encoding"] = "gzip"
            self._send_bytes(
                HTTPStatus.OK,
                payload,
                "application/json; charset=utf-8",
                extra=extra,
                head=True,
            )
            return
        self._not_found()

    def do_POST(self) -> None:
        # POST bodies must never remain on a reusable connection after a
        # rejected capability/origin/schema check.
        self.close_connection = True
        if not self._valid_host():
            self._not_found()
            return
        parsed = urllib.parse.urlsplit(self.path)
        state = self.server.state
        if self.headers.get("Origin") != state.origin:
            self._not_found()
            return

        if parsed.path == f"{state.root_path}/api/connect" and not parsed.query:
            if self.headers.get("Content-Length") not in (None, "0"):
                self._bad_request()
                return
            with state.condition:
                if state.closed.is_set():
                    self._send_empty(HTTPStatus.GONE)
                    return
                state.restore_layout_versions.clear()
                state.protected_layout_versions.clear()
                state.inflight_sequences.clear()
                state.trim_layout_versions()
                state.connection_generation += 1
                generation = state.connection_generation
                state.condition.notify_all()
            self._send_bytes(
                HTTPStatus.OK,
                _compact_json({"generation": generation}),
                "application/json; charset=utf-8",
            )
            return

        generation = (
            0
            if not parsed.query and state.connection_generation == 0
            else self._parse_generation(parsed.query)
        )
        if parsed.path != f"{state.root_path}/api/events" or generation is None:
            self._not_found()
            return

        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip()
        if content_type.lower() != "application/json":
            self._bad_request()
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._bad_request()
            return
        if length <= 0:
            self._bad_request()
            return
        if length > _MAX_EVENT_BYTES:
            # Do not block trying to consume an attacker-controlled declared
            # length.  Closing this connection keeps unread bytes from being
            # interpreted as a pipelined request.
            self.close_connection = True
            self._send_empty(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        if not self.server.reserve_body(length):
            self.close_connection = True
            self._send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
            return

        try:
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    self._bad_request()
                    return
                decoded = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._bad_request()
                return
            message = _validate_bridge_message(decoded)
            if message is None:
                self._bad_request()
                return
            if not state.record_message(message, length, generation=generation):
                self._bad_request()
                return
        finally:
            self.server.release_body(length)
        self._send_empty(HTTPStatus.NO_CONTENT)

    def _serve_state(self, generation: int) -> None:
        state = self.server.state
        with state.condition:
            if generation != state.connection_generation:
                payload = None
            else:
                state.restore_layout_versions[generation] = state.version
                payload = _compact_json(
                    {
                        # This is the last cursor confirmed by a subsequent poll,
                        # not merely the newest command Python has queued.  A new
                        # iframe restores synthesized state, acknowledges older
                        # stateful commands, and replays remaining one-shot work.
                        "sequence": state.applied_sequence,
                        "latestSequence": state.next_sequence - 1,
                        "version": state.version,
                        "path": f"{state.root_path}/layout.json?v={state.version}",
                        "scope": dict(state.current_scope),
                        "visibility": dict(state.current_visibility),
                        "mode": state.current_mode,
                        "view": state.current_view,
                        "selection": state.current_selection,
                        "fit": state.current_fit,
                    }
                )
        if payload is None:
            self._send_empty(HTTPStatus.CONFLICT)
            return
        self._send_bytes(
            HTTPStatus.OK,
            payload,
            "application/json; charset=utf-8",
        )

    def _serve_commands(self, after: int, generation: int) -> None:
        state = self.server.state
        deadline = time.monotonic() + state.poll_timeout
        with state.condition:
            stale = generation != state.connection_generation
            commands: list[dict[str, object]] = []
            superseded = False
            if not stale:
                state.restore_layout_versions.pop(generation, None)
                state.trim_layout_versions()
                if state.inflight_sequences.get(generation, 0) <= after:
                    state.inflight_sequences.pop(generation, None)
                state.poll_ticket += 1
                ticket = state.poll_ticket
                state.condition.notify_all()
                acknowledged = min(after, state.next_sequence - 1)
                state._advance_applied_locked(acknowledged)
                commands = [
                    item for item in state.commands if cast(int, item["seq"]) > after
                ][:1]
                while (
                    not commands
                    and not state.closed.is_set()
                    and ticket == state.poll_ticket
                    and generation == state.connection_generation
                ):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        break
                    state.condition.wait(remaining)
                    commands = [
                        item
                        for item in state.commands
                        if cast(int, item["seq"]) > after
                    ][:1]
                stale = generation != state.connection_generation
                superseded = ticket != state.poll_ticket
                if stale or superseded:
                    commands = []
                if commands:
                    sequence = cast(int, commands[0]["seq"])
                    state.inflight_sequences[generation] = sequence
                    version = state.layout_command_versions.get(sequence)
                    if version is not None and version in state.layout_versions:
                        state.protected_layout_versions[sequence] = version
        payload = _compact_json(
            {
                "commands": commands,
                "closed": state.closed.is_set(),
                "stale": stale,
                "superseded": superseded,
            }
        )
        self._send_bytes(
            HTTPStatus.OK,
            payload,
            "application/json; charset=utf-8",
        )

    @staticmethod
    def _parse_command_query(query: str) -> tuple[int, int] | None:
        try:
            values = urllib.parse.parse_qs(
                query, keep_blank_values=True, strict_parsing=True
            )
        except ValueError:
            return None
        if (
            set(values) not in ({"after"}, {"after", "generation"})
            or len(values["after"]) != 1
            or ("generation" in values and len(values["generation"]) != 1)
        ):
            return None
        raw = values["after"][0]
        if len(raw) > _MAX_CURSOR_DIGITS or not raw.isascii() or not raw.isdecimal():
            return None
        try:
            value = int(raw)
        except ValueError:  # pragma: no cover - guarded for older runtimes
            return None
        if value < 0:
            return None
        if "generation" not in values:
            return value, 0
        raw_generation = values["generation"][0]
        if (
            len(raw_generation) > _MAX_CURSOR_DIGITS
            or not raw_generation.isascii()
            or not raw_generation.isdecimal()
        ):
            return None
        generation = int(raw_generation)
        return (value, generation) if generation > 0 else None

    @staticmethod
    def _parse_layout_query(query: str) -> tuple[bool, int | None]:
        if not query:
            return True, None
        try:
            values = urllib.parse.parse_qs(
                query, keep_blank_values=True, strict_parsing=True
            )
        except ValueError:
            return False, None
        valid = (
            set(values) == {"v"}
            and len(values["v"]) == 1
            and len(values["v"][0]) <= _MAX_CURSOR_DIGITS
            and values["v"][0].isascii()
            and values["v"][0].isdecimal()
        )
        if not valid:
            return False, None
        try:
            return True, int(values["v"][0])
        except ValueError:  # pragma: no cover - guarded for older runtimes
            return False, None

    @staticmethod
    def _parse_generation(query: str) -> int | None:
        try:
            values = urllib.parse.parse_qs(
                query, keep_blank_values=True, strict_parsing=True
            )
        except ValueError:
            return None
        if set(values) != {"generation"} or len(values["generation"]) != 1:
            return None
        raw = values["generation"][0]
        if len(raw) > _MAX_CURSOR_DIGITS or not raw.isascii() or not raw.isdecimal():
            return None
        value = int(raw)
        return value if value > 0 else None

    def _layout_snapshot(
        self, requested_version: int | None
    ) -> tuple[int, tuple[bytes, bytes] | None]:
        state = self.server.state
        version = state.version if requested_version is None else requested_version
        return version, state.layout_versions.get(version)

    def _valid_host(self) -> bool:
        host = self.headers.get("Host")
        if host is None:
            return False
        state = self.server.state
        return host.lower() == f"127.0.0.1:{state.port}"

    def _catalog_file(self, request_path: str) -> _StandaloneFile | None:
        state = self.server.state
        if state.standalone is None:
            return None
        prefix = f"{state.root_path}/viewer/"
        if not request_path.startswith(prefix):
            return None
        encoded_path = request_path[len(prefix) :]
        try:
            relative_path = urllib.parse.unquote(encoded_path, errors="strict")
        except UnicodeDecodeError:
            return None
        return state.standalone.catalog_files.get(relative_path)

    def _send_file(self, asset: _StandaloneFile, *, head: bool = False) -> None:
        try:
            if asset.path.resolve(strict=True) != asset.path:
                self._not_found()
                return
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(asset.path, flags)
        except OSError:
            self._not_found()
            return

        try:
            with os.fdopen(descriptor, "rb") as stream:
                file_stat = os.fstat(stream.fileno())
                if not stat_module.S_ISREG(file_stat.st_mode):
                    self._not_found()
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(file_stat.st_size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Permissions-Policy",
                    "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
                )
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                if asset.content_encoding is not None:
                    self.send_header("Content-Encoding", asset.content_encoding)
                self.end_headers()
                if head:
                    return
                try:
                    remaining = file_stat.st_size
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            # The file was truncated after fstat(). Closing the
                            # connection prevents the next response from being
                            # mistaken for the missing body bytes.
                            self.close_connection = True
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        except OSError:
            # A file removed while it is being served is an ordinary local
            # asset miss; avoid leaking filesystem details to the browser.
            if not self.wfile.closed:
                self.close_connection = True

    def _send_bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        content_type: str,
        *,
        extra: Mapping[str, str] | None = None,
        wrapper: bool = False,
        standalone: bool = False,
        head: bool = False,
    ) -> None:
        state = self.server.state
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
        )
        self.send_header(
            "Cross-Origin-Resource-Policy",
            "cross-origin" if wrapper else "same-origin",
        )
        if wrapper:
            viewer_origin = (
                _url_origin(state.viewer_url)
                if state.viewer_url is not None
                else state.origin
            )
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; "
                f"script-src 'nonce-{state.nonce}'; "
                "connect-src 'self'; "
                f"frame-src {viewer_origin}; "
                "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
            )
        elif standalone:
            # The generated single-file app contains inline module code and
            # styles.  It is trusted, served under an unguessable path, and
            # remains confined to the loopback origin.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self' data: blob:; "
                "script-src 'self' 'unsafe-inline' blob:; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; connect-src 'self'; "
                "worker-src 'self' blob:; frame-ancestors 'self'; "
                "base-uri 'none'; form-action 'none'",
            )
        if extra:
            for name, value in extra.items():
                self.send_header(name, value)
        self.end_headers()
        if not head:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_empty(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def _not_found(self) -> None:
        self._send_bytes(
            HTTPStatus.NOT_FOUND,
            b"Not found\n",
            "text/plain; charset=utf-8",
        )

    def _bad_request(self) -> None:
        self._send_bytes(
            HTTPStatus.BAD_REQUEST,
            b"Bad request\n",
            "text/plain; charset=utf-8",
        )


def _accepts_gzip(value: str) -> bool:
    for part in value.lower().split(","):
        encoding, _, parameters = part.strip().partition(";")
        if encoding != "gzip":
            continue
        for parameter in parameters.split(";"):
            key, separator, raw = parameter.strip().partition("=")
            if separator and key == "q":
                try:
                    return float(raw) > 0.0
                except ValueError:
                    return False
        return True
    return False


def _compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _encode_layout(layout: object) -> tuple[bytes, bytes, _SelectionCatalog]:
    to_dict = getattr(layout, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("layout must provide to_dict()")
    document = to_dict()
    if not isinstance(document, Mapping):
        raise TypeError("layout.to_dict() must return a mapping")
    payload = _compact_json(document)
    return (
        payload,
        gzip.compress(payload, compresslevel=1, mtime=0),
        _selection_catalog(document),
    )


def _selection_catalog(document: Mapping[str, object]) -> _SelectionCatalog:
    curves = cast(Mapping[str, Mapping[str, object]], document["reference_curves"])
    types = cast(Mapping[str, Mapping[str, object]], document["types"])
    objects = cast(Mapping[str, Mapping[str, object]], document["objects"])
    curve_segments = {
        name: len(cast(list[object], curve["segments"]))
        for name, curve in curves.items()
    }
    object_frames: dict[str, frozenset[str]] = {}
    for name, object_value in objects.items():
        type_name = cast(str, object_value["type"])
        type_value = types[type_name]
        frames = cast(Mapping[str, object], type_value["frames"])
        object_frames[name] = frozenset((*_implicit_object_frames(type_value, object_value), *frames))
    return _SelectionCatalog(
        MappingProxyType(curve_segments),
        MappingProxyType(object_frames),
    )


def _implicit_object_frames(type_value: object, object_value: object) -> frozenset[str]:
    declared = getattr(object_value, "implicit_frames", None)
    if declared is not None:
        return frozenset(str(name) for name in declared)

    def has_center(value: object, feature: str) -> bool:
        field = f"{feature}_center"
        return (field in value if isinstance(value, Mapping)
                else getattr(value, field, None) is not None)

    result = {"center"}
    magnetic = has_center(type_value, "magnetic")
    if magnetic:
        result.update(("magnetic_center", "magnetic_entry", "magnetic_exit"))
    if has_center(object_value, "beam") or magnetic:
        result.update(("beam_center", "beam_entry", "beam_exit"))
    return frozenset(result)


def _selection_in_catalog(
    selection: Mapping[str, object],
    catalog: _SelectionCatalog,
) -> bool:
    kind = selection["kind"]
    if kind == "curve":
        name = cast(str, selection["name"])
        count = catalog.curve_segments.get(name)
        if count is None:
            return False
        segment_index = selection.get("segmentIndex")
        return segment_index is None or cast(int, segment_index) < count
    if kind == "object":
        return cast(str, selection["name"]) in catalog.object_frames
    if kind == "frame":
        frames = catalog.object_frames.get(cast(str, selection["object"]))
        return frames is not None and cast(str, selection["name"]) in frames
    return False


def _validate_bridge_message(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    if value.get("source") != _BRIDGE_SOURCE or value.get("protocol") != 1:
        return None
    message_type = value.get("type")
    if message_type == "response":
        command_id = value.get("id")
        if (
            not isinstance(command_id, str)
            or not command_id
            or len(command_id) > 128
            or not isinstance(value.get("ok"), bool)
        ):
            return None
        allowed = {"source", "protocol", "type", "id", "ok", "result", "error"}
        if set(value) - allowed:
            return None
        if value["ok"] is False and not isinstance(value.get("error"), str):
            return None
        return cast(dict[str, object], value)
    if message_type == "event":
        event = value.get("event")
        if not isinstance(event, str) or event not in {
            "ready",
            "selection",
            "layout",
            "closed",
        }:
            return None
        allowed_by_event = {
            "ready": {"source", "protocol", "type", "event"},
            "selection": {"source", "protocol", "type", "event", "selection"},
            "layout": {"source", "protocol", "type", "event", "layout"},
            "closed": {"source", "protocol", "type", "event"},
        }
        if set(value) - allowed_by_event[cast(str, event)]:
            return None
        if event == "selection" and not _valid_selection_payload(
            value.get("selection")
        ):
            return None
        if event == "layout" and not isinstance(value.get("layout"), dict):
            return None
        return cast(dict[str, object], value)
    return None


def _valid_selection_payload(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    kind = value.get("kind")
    if kind == "curve":
        if set(value) - {"kind", "name", "segmentIndex"}:
            return False
        segment_index = value.get("segmentIndex")
        return (
            isinstance(value.get("name"), str)
            and bool(value["name"])
            and len(cast(str, value["name"])) <= 1024
            and (
                "segmentIndex" not in value
                or (
                    isinstance(segment_index, int)
                    and not isinstance(segment_index, bool)
                    and segment_index >= 0
                )
            )
        )
    if kind == "object":
        return (
            set(value) == {"kind", "name"}
            and isinstance(value.get("name"), str)
            and bool(value["name"])
            and len(cast(str, value["name"])) <= 1024
        )
    if kind == "frame":
        return set(value) == {"kind", "object", "name"} and all(
            isinstance(value.get(key), str)
            and bool(value[key])
            and len(cast(str, value[key])) <= 1024
            for key in ("object", "name")
        )
    return False


def _url_origin(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise ValueError("viewer_url must be a valid absolute HTTP(S) URL") from exc
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or host is None:
        raise ValueError("viewer_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("viewer_url must not contain credentials")
    if parsed.fragment:
        raise ValueError("viewer_url must not contain a fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("viewer_url contains an invalid port") from exc
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            normalized_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("viewer_url contains an invalid hostname") from exc
        labels = normalized_host.rstrip(".").split(".")
        if not labels or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(character.isalnum() or character == "-" for character in label)
            for label in labels
        ):
            raise ValueError("viewer_url contains an invalid hostname")
        normalized_host = normalized_host.lower()
        is_loopback = normalized_host.rstrip(".") == "localhost"
    else:
        if getattr(address, "scope_id", None) is not None:
            raise ValueError("viewer_url must not use a scoped IPv6 address")
        is_loopback = address.is_loopback
        normalized_host = (
            f"[{address.compressed}]" if address.version == 6 else str(address)
        )
    if parsed.scheme == "http" and not is_loopback:
        raise ValueError("viewer_url must use HTTPS unless it targets literal loopback")
    default_port = 80 if parsed.scheme == "http" else 443
    authority = (
        normalized_host if port in (None, default_port) else f"{normalized_host}:{port}"
    )
    return f"{parsed.scheme}://{authority}"


def _read_explicit_standalone(path_value: PathLike) -> _StandaloneAsset:
    path = Path(path_value).expanduser()
    if path.is_dir():
        path = path / "index.html"
    try:
        resolved = path.resolve(strict=True)
        file_stat = resolved.stat()
    except OSError as exc:
        raise WebViewerAssetError(
            f"could not read standalone web viewer at {path}"
        ) from exc
    catalog_path = resolved.parent / "list.json"
    try:
        resolved_catalog = catalog_path.resolve(strict=True)
        catalog_stat = resolved_catalog.stat()
        catalog_signature = (
            str(resolved_catalog),
            catalog_stat.st_mtime_ns,
            catalog_stat.st_size,
        )
    except OSError:
        catalog_signature = ("", -1, -1)
    return _read_path_asset(
        str(resolved),
        file_stat.st_mtime_ns,
        file_stat.st_size,
        *catalog_signature,
    )


@lru_cache(maxsize=8)
def _read_path_asset(
    resolved_path: str,
    _mtime_ns: int,
    _size: int,
    resolved_catalog: str,
    _catalog_mtime_ns: int,
    _catalog_size: int,
) -> _StandaloneAsset:
    path = Path(resolved_path)
    catalog_content, catalog_files = _read_standalone_catalog(
        path.parent,
        Path(resolved_catalog) if resolved_catalog else None,
    )
    return _standalone_asset(
        path.read_bytes(),
        resolved_path,
        catalog_content=catalog_content,
        catalog_files=catalog_files,
    )


def _read_standalone_catalog(
    root: Path,
    catalog_path: Path | None,
) -> tuple[bytes | None, Mapping[str, _StandaloneFile]]:
    empty: Mapping[str, _StandaloneFile] = MappingProxyType({})
    if catalog_path is None:
        return None, empty
    try:
        root = root.resolve(strict=True)
        catalog_path.relative_to(root)
        catalog_stat = catalog_path.stat()
        if (
            not stat_module.S_ISREG(catalog_stat.st_mode)
            or catalog_stat.st_size > _MAX_CATALOG_BYTES
        ):
            return None, empty
        content = catalog_path.read_bytes()
    except (OSError, ValueError):
        return None, empty

    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Serve the catalog so the web app can report its normal parse error,
        # but never derive file capabilities from malformed JSON.
        return content, empty

    if isinstance(document, list):
        entries = document
    elif isinstance(document, dict) and isinstance(document.get("layouts"), list):
        entries = document["layouts"]
    elif isinstance(document, dict) and isinstance(document.get("files"), list):
        entries = document["files"]
    else:
        return content, empty

    files: dict[str, _StandaloneFile] = {}
    for raw_entry in entries:
        if isinstance(raw_entry, str):
            raw_path = raw_entry.strip()
        elif isinstance(raw_entry, dict) and isinstance(raw_entry.get("path"), str):
            raw_path = cast(str, raw_entry["path"]).strip()
        else:
            continue
        catalog_file = _resolve_catalog_file(root, raw_path)
        if catalog_file is None:
            continue
        relative_path, asset = catalog_file
        files.setdefault(relative_path, asset)
        if len(files) >= _MAX_CATALOG_ENTRIES:
            break
    return content, MappingProxyType(files)


def _resolve_catalog_file(
    root: Path,
    raw_path: str,
) -> tuple[str, _StandaloneFile] | None:
    if not raw_path or raw_path.startswith(("/", "\\")) or "\\" in raw_path:
        return None
    try:
        unresolved = urllib.parse.urlsplit(raw_path)
        if unresolved.scheme or unresolved.netloc:
            return None
        resolved_url = urllib.parse.urlsplit(
            urllib.parse.urljoin(
                "http://layout-studio.invalid/viewer/list.json",
                raw_path,
            )
        )
    except ValueError:
        return None
    prefix = "/viewer/"
    if (
        resolved_url.scheme != "http"
        or resolved_url.netloc != "layout-studio.invalid"
        or not resolved_url.path.startswith(prefix)
    ):
        return None
    try:
        relative_path = urllib.parse.unquote(
            resolved_url.path[len(prefix) :],
            errors="strict",
        )
    except UnicodeDecodeError:
        return None
    if (
        not relative_path
        or relative_path == "list.json"
        or "\0" in relative_path
        or "\\" in relative_path
        or not relative_path.lower().endswith((".json", ".json.gz"))
    ):
        return None

    try:
        path = (root / relative_path).resolve(strict=True)
        path.relative_to(root)
        file_stat = path.stat()
    except (OSError, ValueError):
        return None
    if not stat_module.S_ISREG(file_stat.st_mode):
        return None
    return (
        relative_path,
        _StandaloneFile(
            path,
            content_encoding="gzip" if relative_path.lower().endswith(".gz") else None,
        ),
    )


def _find_standalone() -> _StandaloneAsset:
    # Editable/source installs should always reuse the repository's generated
    # artifact.  No copy is made into the Python source tree.
    checkout = Path(__file__).resolve().parents[3] / "webapp" / "build" / "index.html"
    if checkout.is_file():
        return _read_explicit_standalone(checkout)

    raise WebViewerAssetError(
        "no local standalone web viewer was found; build "
        "webapp/build/index.html, pass standalone_path=..., or explicitly pass "
        "viewer_url=..."
    )


def _standalone_asset(
    content: bytes,
    label: str,
    *,
    catalog_content: bytes | None = None,
    catalog_files: Mapping[str, _StandaloneFile] | None = None,
) -> _StandaloneAsset:
    return _StandaloneAsset(
        content=content,
        gzip_content=gzip.compress(content, compresslevel=1, mtime=0),
        label=label,
        catalog_content=catalog_content,
        catalog_files=(
            MappingProxyType({}) if catalog_files is None else catalog_files
        ),
    )


def _require_bridge_asset(asset: _StandaloneAsset) -> None:
    if _BRIDGE_MARKER not in asset.content:
        raise WebViewerAssetError(
            f"the standalone web viewer at {asset.label} predates Python bridge "
            "protocol 1; rebuild it with `make -C webapp standalone` or pass an "
            "updated standalone_path/viewer_url"
        )


def _make_wrapper(state: _BridgeState) -> bytes:
    viewer_path = state.viewer_url or f"{state.root_path}/viewer/"
    root = state.root_path
    # Constants are bridge capabilities and paths only.  The layout document is
    # deliberately fetched from layout.json and never inserted in this HTML.
    script = f"""
(() => {{
  'use strict';
  const SOURCE = {_js(_BRIDGE_SOURCE)};
  const PROTOCOL = {_BRIDGE_PROTOCOL};
  const NONCE = {_js(state.nonce)};
  const ROOT = {_js(root)};
  const viewer = document.getElementById('viewer');
  const status = document.getElementById('status');
  const viewerUrl = new URL({_js(viewer_path)}, window.location.href);
  const viewerOrigin = viewerUrl.origin;
  const fragment = new URLSearchParams({{
    'python-bridge': NONCE,
    'python-origin': window.location.origin,
  }});
  viewerUrl.hash = fragment.toString();

  let port = null;
  let pollController = null;
  let lastSequence = 0;
  let restoredThrough = 0;
  let polling = false;
  let bridgeReady = false;
  let stateRestored = false;
  let connectionGeneration = 0;
  let serverGeneration = 0;
  let connectAttempts = 0;
  let connectTimer = null;
  let reportChain = Promise.resolve();
  let reportTailItem = null;
  let pendingEventReports = new Map();
  let registrationChain = Promise.resolve();
  const responseWaiters = new Map();

  function report(
    message,
    generation,
    expectedGeneration = connectionGeneration,
  ) {{
    const eventKey = message?.type === 'event' &&
      (message.event === 'selection' || message.event === 'layout')
      ? message.event
      : null;
    const queued = eventKey ? pendingEventReports.get(eventKey) : null;
    if (
      queued && queued === reportTailItem &&
      queued.generation === generation &&
      queued.localGeneration === expectedGeneration
    ) {{
      // Selection/layout notifications are snapshots, not deltas.  Keep only
      // the newest unsent value so rapid UI activity cannot grow this queue.
      queued.message = message;
      return queued.promise;
    }}

    const item = {{
      message,
      generation,
      localGeneration: expectedGeneration,
      promise: null,
    }};
    const previous = reportChain;
    const pending = previous.then(async () => {{
      if (eventKey && pendingEventReports.get(eventKey) === item) {{
        // Once this value is serialized/in flight, a later event needs its
        // own trailing slot rather than mutating a message already sent.
        pendingEventReports.delete(eventKey);
      }}
      const isCurrent = () =>
        item.localGeneration === connectionGeneration &&
        item.generation === serverGeneration;
      if (
        !Number.isSafeInteger(item.generation) || item.generation <= 0 ||
        !isCurrent()
      ) return false;
      const post = payload => fetch(
        `${{ROOT}}/api/events?generation=${{item.generation}}`,
        {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify(payload),
          cache: 'no-store',
          credentials: 'same-origin',
        }},
      );
      try {{
        const response = await post(item.message);
        if (
          response.status === 413 && isCurrent() &&
          item.message?.type === 'response' &&
          typeof item.message.id === 'string'
        ) {{
          // A large get_layout result must terminate its command instead of
          // being replayed forever and blocking every command behind it.
          const terminal = await post({{
            source: SOURCE,
            protocol: PROTOCOL,
            type: 'response',
            id: item.message.id,
            ok: false,
            error: 'viewer response exceeds the 32 MiB bridge limit',
          }});
          return terminal.ok && isCurrent();
        }}
        return response.ok && isCurrent();
      }} catch (_) {{
        return false;
      }}
    }});
    item.promise = pending;
    // MessagePort handlers can overlap.  Serialize their HTTP reports so
    // selection and response order remains identical to port message order.
    reportChain = pending.then(() => undefined, () => undefined);
    reportTailItem = item;
    if (eventKey) {{
      pendingEventReports.set(eventKey, item);
    }}
    void pending.finally(() => {{
      if (eventKey && pendingEventReports.get(eventKey) === item) {{
        pendingEventReports.delete(eventKey);
      }}
      if (reportTailItem === item) reportTailItem = null;
    }});
    return pending;
  }}

  function registerConnection() {{
    const pending = registrationChain.then(async () => {{
      const response = await fetch(`${{ROOT}}/api/connect`, {{
        method: 'POST', cache: 'no-store', credentials: 'same-origin',
      }});
      if (!response.ok) {{
        throw new Error(`connection request failed (${{response.status}})`);
      }}
      const payload = await response.json();
      const generation = Number(payload.generation);
      if (!Number.isSafeInteger(generation) || generation <= 0) {{
        throw new Error('connection request returned an invalid generation');
      }}
      return generation;
    }});
    registrationChain = pending.then(() => undefined, () => undefined);
    return pending;
  }}

  async function relay(
    command,
    reportResponse = true,
    expectedGeneration = connectionGeneration,
    expectedServerGeneration = serverGeneration,
  ) {{
    const targetPort = port;
    if (!targetPort) throw new Error('Python bridge port is unavailable');
    const isCurrent = () =>
      targetPort === port &&
      expectedGeneration === connectionGeneration &&
      expectedServerGeneration === serverGeneration;
    const outgoing = {{...command}};
    delete outgoing.seq;
    if (outgoing.command === 'load_layout') {{
      try {{
        const target = new URL(String(outgoing.path), window.location.href);
        if (target.origin !== window.location.origin ||
            !target.pathname.startsWith(`${{ROOT}}/`)) {{
          throw new Error('rejected non-local layout path');
        }}
        const response = await fetch(target.href, {{cache: 'no-store'}});
        if (!response.ok) throw new Error(`layout request failed (${{response.status}})`);
        const expectedVersion = Number(
          outgoing.expectedVersion ?? target.searchParams.get('v'),
        );
        const actualVersion = Number(
          response.headers.get('X-Layout-Studio-Version'),
        );
        if (
          Number.isSafeInteger(expectedVersion) && expectedVersion > 0 &&
          actualVersion !== expectedVersion
        ) {{
          throw new Error('layout snapshot changed during restore');
        }}
        outgoing.layout = await response.json();
        if (!isCurrent()) throw new Error('Python bridge connection changed');
        outgoing.command = 'set_layout';
        delete outgoing.path;
        delete outgoing.expectedVersion;
      }} catch (error) {{
        if (!isCurrent()) throw new Error('Python bridge connection changed');
        if (!reportResponse) throw error;
        const reported = await report(
          {{
            source: SOURCE, protocol: PROTOCOL, type: 'response',
            id: String(outgoing.id), ok: false,
            error: error instanceof Error ? error.message : String(error),
          }},
          expectedServerGeneration,
          expectedGeneration,
        );
        if (!reported || !isCurrent()) {{
          throw new Error('could not report layout request failure');
        }}
        return;
      }}
    }}
    if (!isCurrent()) throw new Error('Python bridge connection changed');
    await new Promise((resolve, reject) => {{
      const id = String(outgoing.id);
      const waiter = {{
        port: targetPort,
        localGeneration: expectedGeneration,
        serverGeneration: expectedServerGeneration,
        report: reportResponse,
        resolve: () => {{
          window.clearTimeout(waiter.timer);
          resolve();
        }},
        reject: error => {{
          window.clearTimeout(waiter.timer);
          reject(error);
        }},
        timer: 0,
      }};
      waiter.timer = window.setTimeout(() => {{
        if (responseWaiters.get(id) === waiter) responseWaiters.delete(id);
        reject(new Error(`viewer response timed out for ${{id}}`));
      }}, 15000);
      responseWaiters.set(id, waiter);
      targetPort.postMessage(outgoing);
    }});
    if (!isCurrent()) throw new Error('Python bridge connection changed');
  }}

  function settleResponseWaiters(error = new Error('Python bridge connection changed')) {{
    const waiters = [...responseWaiters.values()];
    responseWaiters.clear();
    for (const waiter of waiters) waiter.reject(error);
  }}

  async function restoreState(generation, registeredGeneration, connectedPort) {{
    const isCurrent = () =>
      generation === connectionGeneration &&
      registeredGeneration === serverGeneration &&
      connectedPort === port;
    try {{
      const response = await fetch(
        `${{ROOT}}/api/state?generation=${{registeredGeneration}}`,
        {{cache: 'no-store'}},
      );
      if (!response.ok) throw new Error(`state request failed (${{response.status}})`);
      const snapshot = await response.json();
      if (!isCurrent()) return false;
      const base = {{source: SOURCE, protocol: PROTOCOL, type: 'command'}};
      const load = {{
        ...base,
        id: `restore-${{generation}}-layout`,
        command: 'load_layout',
        path: snapshot.path,
        expectedVersion: snapshot.version,
        scope: snapshot.scope,
      }};
      if (snapshot.visibility && Object.keys(snapshot.visibility).length) {{
        load.visibility = snapshot.visibility;
      }}
      await relay(load, false, generation, registeredGeneration);
      if (!isCurrent()) return false;
      if (snapshot.mode) {{
        await relay({{
          ...base, id: `restore-${{generation}}-mode`,
          command: 'set_mode', mode: snapshot.mode,
        }}, false, generation, registeredGeneration);
        if (!isCurrent()) return false;
      }}
      if (snapshot.view) {{
        await relay({{
          ...base, id: `restore-${{generation}}-view`,
          command: 'set_view', view: snapshot.view,
        }}, false, generation, registeredGeneration);
        if (!isCurrent()) return false;
      }}
      await relay({{
        ...base, id: `restore-${{generation}}-selection`,
        command: 'set_selection', selection: snapshot.selection,
      }}, false, generation, registeredGeneration);
      if (!isCurrent()) return false;
      if (snapshot.fit) {{
        await relay({{
          ...base, id: `restore-${{generation}}-fit`,
          command: 'fit', target: snapshot.fit,
        }}, false, generation, registeredGeneration);
        if (!isCurrent()) return false;
      }}
      lastSequence = Number(snapshot.sequence) || 0;
      restoredThrough = Number(snapshot.latestSequence) || lastSequence;
      stateRestored = true;
      return true;
    }} catch (error) {{
      if (isCurrent()) {{
        status.hidden = false;
        status.textContent = error instanceof Error ? error.message : 'State restore failed';
      }}
      return false;
    }}
  }}

  async function poll(generation, registeredGeneration, connectedPort) {{
    if (polling) return;
    polling = true;
    const controller = new AbortController();
    pollController = controller;
    const isCurrent = () =>
      generation === connectionGeneration &&
      registeredGeneration === serverGeneration &&
      connectedPort === port;
    while (isCurrent() && !controller.signal.aborted) {{
      try {{
        const response = await fetch(
          `${{ROOT}}/api/commands?after=${{lastSequence}}&generation=${{registeredGeneration}}`,
          {{cache: 'no-store', signal: controller.signal}},
        );
        if (!response.ok) throw new Error(`command request failed (${{response.status}})`);
        const packet = await response.json();
        if (!isCurrent()) break;
        if (packet.stale) {{
          void connect();
          break;
        }}
        for (const command of packet.commands || []) {{
          const sequence = Number(command.seq);
          if (!Number.isSafeInteger(sequence) || sequence <= lastSequence) continue;
          const representedBySnapshot = sequence <= restoredThrough && [
            'load_layout', 'set_scope', 'set_visibility', 'set_mode',
            'set_view', 'set_selection', 'fit',
          ].includes(command.command);
          const invalidatedReadback =
            sequence <= restoredThrough && command.command === 'get_layout';
          if (invalidatedReadback) {{
            const reported = await report({{
              source: SOURCE, protocol: PROTOCOL, type: 'response',
              id: String(command.id), ok: false,
              error: 'readback invalidated by viewer reconnect',
            }}, registeredGeneration, generation);
            if (!reported) throw new Error('could not reject invalidated readback');
          }} else if (representedBySnapshot) {{
            const reported = await report({{
              source: SOURCE, protocol: PROTOCOL, type: 'response',
              id: String(command.id), ok: true,
              result: {{restored: true}},
            }}, registeredGeneration, generation);
            if (!reported) throw new Error('could not acknowledge restored command');
          }} else {{
            await relay(command, true, generation, registeredGeneration);
          }}
          if (!isCurrent()) break;
          lastSequence = sequence;
        }}
        if (packet.closed) break;
      }} catch (error) {{
        if (controller.signal.aborted || !isCurrent()) break;
        status.textContent = 'Python bridge disconnected';
        void connect();
        break;
      }}
    }}
    if (pollController === controller) pollController = null;
    polling = false;
  }}

  function startPolling(generation, registeredGeneration, connectedPort) {{
    if (!bridgeReady || !stateRestored) return;
    if (polling) {{
      window.setTimeout(
        () => startPolling(generation, registeredGeneration, connectedPort),
        10,
      );
      return;
    }}
    void poll(generation, registeredGeneration, connectedPort);
  }}

  function scheduleConnect(generation) {{
    if (generation !== connectionGeneration || bridgeReady) return;
    if (connectTimer !== null) window.clearTimeout(connectTimer);
    if (connectAttempts < 20) {{
      connectTimer = window.setTimeout(() => void connect(), 500);
    }} else {{
      status.hidden = false;
      status.textContent = 'Python bridge unavailable';
    }}
  }}

  async function connect() {{
    if (pollController) pollController.abort();
    if (port) port.close();
    settleResponseWaiters();
    port = null;
    bridgeReady = false;
    stateRestored = false;
    connectionGeneration += 1;
    // Detach queued reports from the obsolete generation.  Their callbacks
    // see the generation change and exit without issuing another request.
    reportChain = Promise.resolve();
    reportTailItem = null;
    pendingEventReports = new Map();
    connectAttempts += 1;
    const generation = connectionGeneration;
    let registeredGeneration;
    try {{
      registeredGeneration = await registerConnection();
    }} catch (error) {{
      if (generation === connectionGeneration) {{
        status.hidden = false;
        status.textContent = error instanceof Error
          ? error.message
          : 'Python bridge registration failed';
        scheduleConnect(generation);
      }}
      return;
    }}
    if (generation !== connectionGeneration) return;
    serverGeneration = registeredGeneration;

    const channel = new MessageChannel();
    const connectedPort = channel.port1;
    port = connectedPort;
    const isCurrent = () =>
      connectedPort === port &&
      generation === connectionGeneration &&
      registeredGeneration === serverGeneration;
    connectedPort.onmessage = async event => {{
      if (!isCurrent()) return;
      const message = event.data;
      if (!message || message.source !== SOURCE || message.protocol !== PROTOCOL) return;
      if (message.type !== 'response' && message.type !== 'event') return;
      if (message.type === 'response') {{
        const id = String(message.id);
        const waiter = responseWaiters.get(id);
        if (
          !waiter || waiter.port !== connectedPort ||
          waiter.localGeneration !== generation ||
          waiter.serverGeneration !== registeredGeneration
        ) return;
        responseWaiters.delete(id);
        if (!message.ok && !waiter.report) {{
          waiter.reject(new Error(String(message.error || 'viewer rejected restore command')));
          return;
        }}
        if (waiter.report) {{
          const reported = await report(
            message,
            registeredGeneration,
            generation,
          );
          if (!reported || !isCurrent()) {{
            waiter.reject(new Error('could not report viewer response'));
            return;
          }}
        }}
        waiter.resolve();
        return;
      }}
      if (message.type === 'event' && message.event === 'ready') {{
        if (connectTimer !== null) window.clearTimeout(connectTimer);
        connectTimer = null;
        const restored = await restoreState(
          generation,
          registeredGeneration,
          connectedPort,
        );
        if (!isCurrent()) return;
        if (restored) {{
          const reported = await report(
            message,
            registeredGeneration,
            generation,
          );
          if (!reported || !isCurrent()) {{
            stateRestored = false;
            scheduleConnect(generation);
            return;
          }}
          bridgeReady = true;
          connectAttempts = 0;
          status.hidden = true;
          startPolling(generation, registeredGeneration, connectedPort);
        }} else {{
          scheduleConnect(generation);
        }}
        return;
      }}
      if (message.type === 'event' && message.event === 'selection' &&
          !stateRestored) return;
      await report(message, registeredGeneration, generation);
    }};
    connectedPort.start();
    viewer.contentWindow.postMessage(
      {{source: SOURCE, protocol: PROTOCOL, type: 'connect', nonce: NONCE}},
      viewerOrigin,
      [channel.port2],
    );
    scheduleConnect(generation);
  }}

	  viewer.addEventListener('load', () => {{
	    bridgeReady = false;
	    stateRestored = false;
	    lastSequence = 0;
	    restoredThrough = 0;
	    connectAttempts = 0;
    if (connectTimer !== null) window.clearTimeout(connectTimer);
    void connect();
  }});
  viewer.src = viewerUrl.href;
  window.addEventListener('beforeunload', () => {{
    if (connectTimer !== null) window.clearTimeout(connectTimer);
    if (pollController) pollController.abort();
    if (port) port.close();
    settleResponseWaiters(new Error('Python bridge page is closing'));
    const payload = new Blob([JSON.stringify({{
      source: SOURCE, protocol: PROTOCOL, type: 'event', event: 'closed',
    }})], {{type: 'application/json'}});
    navigator.sendBeacon(
      `${{ROOT}}/api/events?generation=${{serverGeneration}}`,
      payload,
    );
  }});
}})();
"""
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Layout Studio Python viewer</title>
<style>html,body,#viewer{{border:0;height:100%;margin:0;width:100%}}#status{{background:#111827;color:#f9fafb;font:14px system-ui;padding:8px 12px;position:fixed;right:12px;top:12px;z-index:2}}</style>
</head>
<body>
<div id="status" role="status">Connecting to Python…</div>
<iframe id="viewer" title="Layout Studio web viewer"
  sandbox="allow-downloads allow-same-origin allow-scripts"
  referrerpolicy="no-referrer"></iframe>
<script nonce="{escape(state.nonce)}">{script}</script>
</body>
</html>"""
    return html.encode("utf-8")


def _js(value: str) -> str:
    # JSON string encoding is the correct JavaScript string literal encoding;
    # escape '<' to keep values incapable of terminating the script element.
    return json.dumps(value).replace("<", "\\u003c")


def _shutdown(
    server: _BridgeServer,
    thread: threading.Thread,
    state: _BridgeState,
) -> None:
    with state.condition:
        if state.closed.is_set():
            return
        state.closed.set()
        # Wake readiness waiters; they check ``closed`` before succeeding.
        state.ready.set()
        state.condition.notify_all()
    if thread.is_alive():
        server.shutdown()
    server.close_active_requests()
    server.wait_for_handlers(timeout=2.0)
    server.server_close()
    if thread is not threading.current_thread():
        thread.join(timeout=2.0)
    with state.condition:
        state.layout_bytes = b""
        state.layout_gzip = b""
        state.layout_versions.clear()
        state.commands.clear()
        state.layout_command_versions.clear()
        state.protected_layout_versions.clear()
        state.restore_layout_versions.clear()
        state.inflight_sequences.clear()
        state.responses.clear()
        state.response_sizes.clear()
        state.completed_ranges.clear()
        state.response_bytes = 0
        while True:
            try:
                state.events.get_nowait()
            except queue.Empty:
                break
        state.event_bytes = 0


class WebViewer:
    """Serve and control the Layout Studio web viewer from Python.

    Construction starts a daemon loopback server and returns immediately.  By
    default the class locates a locally built source-checkout asset;
    ``viewer_url`` opts into an externally hosted compatible app instead.  Use
    :meth:`show` to open a browser, or return/display the object in IPython to
    use its HTML representation.
    """

    def __init__(
        self,
        layout: object,
        *,
        standalone_path: PathLike | None = None,
        viewer_url: str | None = None,
        scope: object | None = None,
        selection: object | None = None,
        fit: object | None = None,
        mode: ViewerMode | None = None,
        visibility: Mapping[str, bool] | None = None,
        show: bool = False,
        width: str | int = "100%",
        height: int = _DEFAULT_HEIGHT,
        poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
    ) -> None:
        if standalone_path is not None and viewer_url is not None:
            raise TypeError("standalone_path and viewer_url are mutually exclusive")
        if viewer_url is not None:
            _url_origin(viewer_url)
            standalone = None
        else:
            standalone = (
                _read_explicit_standalone(standalone_path)
                if standalone_path is not None
                else _find_standalone()
            )
            _require_bridge_asset(standalone)
        if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
            raise ValueError("height must be a positive integer")
        if not isinstance(width, (str, int)) or isinstance(width, bool):
            raise TypeError("width must be a CSS string or integer pixel count")
        if isinstance(width, int) and width <= 0:
            raise ValueError("integer width must be positive")
        poll_timeout_value = _timeout_value(poll_timeout, "poll_timeout")
        if poll_timeout_value is None or poll_timeout_value == 0.0:
            raise ValueError("poll_timeout must be positive")

        normalized_scope = (
            {"kind": "layout"} if scope is None else _fit_target(scope, layout)
        )
        normalized_visibility = _visibility_values(visibility)
        if mode is not None and mode not in {"orbit", "pan", "select", "zoom-region"}:
            raise ValueError("mode must be 'orbit', 'pan', 'select', or 'zoom-region'")
        normalized_selection = (
            None
            if selection is None
            else _checked_selection_target(selection, layout, normalized_scope)
        )
        if normalized_selection is not None and not _selection_in_scope(
            normalized_selection, normalized_scope
        ):
            raise ValueError("selection is outside the current web viewer scope")
        normalized_fit = None if fit is None else _fit_target(fit, layout)
        if normalized_fit is not None and not _fit_in_scope(
            normalized_fit, normalized_scope
        ):
            raise ValueError("fit target is outside the current web viewer scope")

        payload, compressed, selection_catalog = _encode_layout(layout)
        state = _BridgeState(
            token=secrets.token_urlsafe(16),
            nonce=secrets.token_urlsafe(16),
            layout_bytes=payload,
            layout_gzip=compressed,
            standalone=standalone,
            viewer_url=viewer_url,
            selection_catalog=selection_catalog,
            poll_timeout=poll_timeout_value,
            current_scope=dict(normalized_scope),
            current_visibility=dict(normalized_visibility),
            current_mode=mode,
            current_selection=normalized_selection,
            current_fit=normalized_fit,
        )
        state.layout_versions[1] = (payload, compressed)
        server = _BridgeServer(state)
        state.port = cast(int, server.server_address[1])
        state.wrapper_html = _make_wrapper(state)
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name=f"layout-studio-web-{state.port}",
            daemon=True,
        )
        thread.start()

        self._layout = layout
        self._state = state
        self._server = server
        self._thread = thread
        self._width = f"{width}px" if isinstance(width, int) else width
        self._height = height
        self._scope = normalized_scope
        self._visibility = normalized_visibility
        self._finalizer = weakref.finalize(self, _shutdown, server, thread, state)

        try:
            self._enqueue_layout(include_view_controls=True)
            if show:
                self.show()
        except BaseException:
            self.close()
            raise

    @property
    def layout(self) -> object:
        """The layout object serialized by the most recent :meth:`update`."""

        return self._layout

    @property
    def url(self) -> str:
        """Capability URL for the local wrapper page."""

        return f"{self._state.origin}{self._state.root_path}/"

    @property
    def closed(self) -> bool:
        return self._state.closed.is_set()

    def update(self, layout: object | None = None) -> str:
        """Refresh the cached document and enqueue a browser update.

        Mutating a layout does not serialize it on every edit.  Call ``update``
        when a new snapshot should be shown.  The returned command id can be
        passed to :meth:`wait_response` when acknowledgement is required.
        """

        self._ensure_open()
        candidate = self._layout if layout is None else layout
        payload, compressed, selection_catalog = _encode_layout(candidate)
        with self._state.condition:
            self._ensure_open_locked()
            # Selection, fit, or scope may have changed while the potentially
            # expensive serialization ran.  Reconcile the latest state at the
            # atomic commit point instead of overwriting a browser event.
            scope = dict(self._scope)
            _fit_target(scope, candidate)
            candidate_selection = self._state.current_selection
            if candidate_selection is not None:
                try:
                    candidate_selection = _checked_selection_target(
                        candidate_selection, candidate, scope
                    )
                except (KeyError, TypeError, ValueError):
                    candidate_selection = None
            candidate_fit = self._state.current_fit
            if candidate_fit is not None:
                try:
                    candidate_fit = _fit_target(candidate_fit, candidate)
                    if not _fit_in_scope(candidate_fit, scope):
                        candidate_fit = {"kind": "layout"}
                except (KeyError, TypeError, ValueError):
                    candidate_fit = {"kind": "layout"}
            self._layout = candidate
            self._state.selection_catalog = selection_catalog
            self._state.current_selection = candidate_selection
            self._state.current_fit = candidate_fit
            self._state.layout_bytes = payload
            self._state.layout_gzip = compressed
            self._state.version += 1
            version = self._state.version
            self._state.layout_versions[version] = (
                payload,
                compressed,
            )
            self._state.trim_layout_versions()
            return self._enqueue_layout(version=version)

    def select(self, target: object | None = None) -> str:
        """Select a curve, object, or object frame; clear with ``None``."""

        with self._state.condition:
            self._ensure_open_locked()
            selection = (
                None
                if target is None
                else _checked_selection_target(target, self._layout, self._scope)
            )
            if selection is not None and not _selection_in_scope(
                selection, self._scope
            ):
                raise ValueError("selection is outside the current web viewer scope")
            self._state.current_selection = selection
            return self._state.enqueue("set_selection", selection=selection)

    def fit(self, target: object | None = None) -> str:
        """Fit the whole layout or one named curve/object in the viewport."""

        with self._state.condition:
            self._ensure_open_locked()
            fit_target = (
                {"kind": "layout"}
                if target is None
                else _fit_target(target, self._layout)
            )
            if not _fit_in_scope(fit_target, self._scope):
                raise ValueError("fit target is outside the current web viewer scope")
            self._state.current_fit = fit_target
            return self._state.enqueue("fit", target=fit_target)

    def set_scope(self, target: object | None = None) -> str:
        """Render the whole layout or only one curve/object.

        The complete document remains loaded in the browser so references can
        still be resolved; scope only limits scene construction and picking.
        """

        with self._state.condition:
            self._ensure_open_locked()
            scope = (
                {"kind": "layout"}
                if target is None
                else _fit_target(target, self._layout)
            )
            self._scope = scope
            self._state.current_scope = dict(scope)
            selection = self._state.current_selection
            if selection is not None and not _selection_in_scope(selection, scope):
                self._state.current_selection = None
            fit_target = self._state.current_fit
            if fit_target is not None and not _fit_in_scope(fit_target, scope):
                self._state.current_fit = {"kind": "layout"}
            return self._state.enqueue("set_scope", scope=dict(scope))

    def set_mode(self, mode: ViewerMode) -> str:
        """Switch interaction mode, including rectangular ``zoom-region``."""

        if mode not in {"orbit", "pan", "select", "zoom-region"}:
            raise ValueError("mode must be 'orbit', 'pan', 'select', or 'zoom-region'")
        with self._state.condition:
            self._ensure_open_locked()
            self._state.current_mode = mode
            return self._state.enqueue("set_mode", mode=mode)

    def set_view(self, direction: ViewerDirection) -> str:
        """Align the camera with one of the six signed world axes."""

        if direction not in {"+x", "-x", "+y", "-y", "+z", "-z"}:
            raise ValueError("direction must be one of +x, -x, +y, -y, +z, -z")
        with self._state.condition:
            self._ensure_open_locked()
            self._state.current_view = direction
            return self._state.enqueue("set_view", view=direction)

    def set_visibility(
        self,
        *,
        curves: bool | None = None,
        objects: bool | None = None,
        magnetic_axis: bool | None = None,
        beam_axis: bool | None = None,
        frames: bool | None = None,
    ) -> str:
        """Update any subset of the web viewer's layer toggles."""

        supplied = _visibility_values(
            {
                "curves": curves,
                "objects": objects,
                "magnetic_axis": magnetic_axis,
                "beam_axis": beam_axis,
                "frames": frames,
            },
            require_value=True,
        )
        with self._state.condition:
            self._ensure_open_locked()
            self._visibility.update(supplied)
            self._state.current_visibility = dict(self._visibility)
            return self._state.enqueue("set_visibility", visibility=supplied)

    def request_layout(self) -> str:
        """Request the browser's current editable layout without blocking.

        Call :meth:`wait_ready` first.  Unlike state-setting commands, a
        readback queued before initial restoration or across an iframe
        reconnect cannot be replayed without changing its historical meaning.
        """

        return self._state.enqueue("get_layout")

    def get_event(self, timeout: float | None = 0.0) -> dict[str, object] | None:
        """Return the next browser event; do not block by default."""

        timeout_value = _timeout_value(timeout, "timeout", allow_none=True)
        if timeout_value == 0.0:
            try:
                return self._state.pop_event()
            except queue.Empty:
                return None
        deadline = None if timeout_value is None else time.monotonic() + timeout_value
        while True:
            try:
                return self._state.pop_event(
                    timeout=0.1
                    if deadline is None
                    else min(0.1, max(0.0, deadline - time.monotonic()))
                )
            except queue.Empty:
                if self.closed or (
                    deadline is not None and time.monotonic() >= deadline
                ):
                    return None

    def wait_ready(self, timeout: float | None = 10.0) -> Self:
        """Wait for bridge protocol 1, raising a diagnostic timeout on failure."""

        timeout_value = _timeout_value(timeout, "timeout", allow_none=True)
        deadline = None if timeout_value is None else time.monotonic() + timeout_value
        while not self.closed:
            remaining = (
                0.1
                if deadline is None
                else min(0.1, max(0.0, deadline - time.monotonic()))
            )
            if self._state.ready.wait(remaining):
                if not self.closed:
                    return self
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
        if self.closed:
            raise WebViewerError("web viewer closed before the bridge became ready")
        asset = (
            self._state.standalone.label
            if self._state.standalone
            else cast(str, self._state.viewer_url)
        )
        raise WebViewerTimeoutError(
            "the browser did not complete Layout Studio Python bridge protocol 1 "
            f"using {asset}; ensure the viewer is open and rebuild/update the web "
            "app if this asset predates the bridge"
        )

    def wait_response(
        self,
        command_id: str,
        timeout: float | None = 10.0,
    ) -> dict[str, object]:
        """Wait explicitly for one command response and remove it from the cache."""

        if not isinstance(command_id, str) or not command_id:
            raise TypeError("command_id must be a non-empty string")
        timeout_value = _timeout_value(timeout, "timeout", allow_none=True)
        deadline = None if timeout_value is None else time.monotonic() + timeout_value
        with self._state.condition:
            while command_id not in self._state.responses:
                if self.closed:
                    raise WebViewerError(
                        "web viewer closed before receiving a response"
                    )
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0.0:
                    raise WebViewerTimeoutError(
                        f"no response for web viewer command {command_id!r}"
                    )
                self._state.condition.wait(remaining)
            response = self._state.responses.pop(command_id)
            self._state.response_bytes -= self._state.response_sizes.pop(command_id, 0)
            return response

    def show(self) -> Self:
        """Open the wrapper in the system browser and return immediately."""

        self._ensure_open()
        webbrowser.open(self.url, new=2, autoraise=True)
        return self

    def close(self) -> None:
        """Stop the loopback server.  Repeated calls are harmless."""

        if self._finalizer.alive:
            self._finalizer()

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        del exc_info
        self.close()

    def _repr_html_(self) -> str:
        if self.closed:
            return "<p>Layout Studio web viewer is closed.</p>"
        return (
            '<iframe title="Layout Studio web viewer" '
            f'src="{escape(self.url, quote=True)}" '
            f'style="border:0;height:{self._height}px;width:{escape(self._width, quote=True)}" '
            'loading="eager"></iframe>'
        )

    def _enqueue_layout(
        self,
        *,
        version: int | None = None,
        include_view_controls: bool = False,
    ) -> str:
        snapshot_version = self._state.version if version is None else version
        path = f"{self._state.root_path}/layout.json?v={snapshot_version}"
        fields: dict[str, object] = {
            "path": path,
            "scope": dict(self._scope),
            "selection": self._state.current_selection,
        }
        if self._visibility:
            fields["visibility"] = dict(self._visibility)
        if include_view_controls:
            if self._state.current_mode is not None:
                fields["mode"] = self._state.current_mode
            if self._state.current_view is not None:
                fields["view"] = self._state.current_view
            if self._state.current_fit is not None:
                fields["fit"] = self._state.current_fit
        return self._state.enqueue(
            "load_layout", layout_version=snapshot_version, **fields
        )

    def _ensure_open(self) -> None:
        if self.closed:
            raise WebViewerError("web viewer is closed")

    def _ensure_open_locked(self) -> None:
        if self._state.closed.is_set():
            raise WebViewerError("web viewer is closed")


def _fit_target(value: object, layout: object | None = None) -> dict[str, str]:
    if isinstance(value, Mapping):
        kind = value.get("kind")
        name = value.get("name")
    elif (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(item, str) for item in value)
    ):
        kind, name = value
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith("curve:"):
            kind, name = "curve", text[6:]
        elif text.startswith("object:"):
            kind, name = "object", text[7:]
        elif layout is not None:
            curves = getattr(layout, "curves", {})
            objects = getattr(layout, "objects", {})
            in_curves = text in curves
            in_objects = text in objects
            if in_curves and in_objects:
                raise ValueError(
                    f"target name {text!r} is ambiguous; use 'curve:' or 'object:'"
                )
            if in_curves:
                kind, name = "curve", text
            elif in_objects:
                kind, name = "object", text
            else:
                raise KeyError(f"unknown fit/scope entity {text!r}")
        else:
            raise TypeError("string targets require a layout")
    else:
        class_name = type(value).__name__.lower()
        kind = class_name if class_name in {"layout", "curve", "object"} else None
        name = getattr(value, "name", None)
    allowed = {"layout", "curve", "object"}
    if kind not in allowed:
        raise TypeError("target must identify a layout, curve, or object")
    if kind == "layout":
        if (
            layout is not None
            and type(value).__name__.lower() == "layout"
            and value is not layout
        ):
            raise ValueError("target layout is not this web viewer's layout")
        return {"kind": "layout"}
    if not isinstance(name, str) or not name:
        raise ValueError("target must have a non-empty attached name")
    if layout is not None:
        registry = getattr(layout, "curves" if kind == "curve" else "objects", {})
        if name not in registry:
            raise KeyError(f"unknown {kind} {name!r}")
        attached = registry[name]
        if type(value).__name__.lower() == kind and attached is not value:
            raise ValueError(f"target {kind} does not belong to this layout")
    return {"kind": cast(str, kind), "name": name}


def _selection_target(
    value: object,
    layout: object,
    scope: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if isinstance(value, Mapping):
        kind = value.get("kind")
        if kind == "frame":
            return _frame_target(value.get("object"), value.get("name"))
        result: dict[str, object] = _named_selection(kind, value.get("name"))
        if kind == "curve" and "segmentIndex" in value:
            segment_index = value["segmentIndex"]
            if (
                isinstance(segment_index, bool)
                or not isinstance(segment_index, int)
                or segment_index < 0
            ):
                raise ValueError("curve segmentIndex must be a non-negative integer")
            result["segmentIndex"] = segment_index
        return result

    if isinstance(value, tuple) and len(value) == 2:
        kind, item = value
        if kind == "frame":
            if isinstance(item, tuple) and len(item) == 2:
                return _frame_target(item[0], item[1])
            if isinstance(item, str):
                return _frame_target_from_text(item)
            raise TypeError(
                "frame tuple must contain 'object.frame' or (object, frame)"
            )
        return _named_selection(kind, item)

    if isinstance(value, str):
        if value.startswith("curve:"):
            return _named_selection("curve", value[6:])
        if value.startswith("object:"):
            return _named_selection("object", value[7:])
        if "->" in value or "." in value:
            return _frame_target_from_text(value)
        curves = getattr(layout, "curves", {})
        objects = getattr(layout, "objects", {})
        is_curve = value in curves
        is_object = value in objects
        if is_curve and is_object:
            raise ValueError(
                f"selection name {value!r} is ambiguous; use 'curve:' or 'object:'"
            )
        if is_curve:
            return _named_selection("curve", value)
        if is_object:
            return _named_selection("object", value)
        raise KeyError(f"unknown selectable entity {value!r}")

    class_name = type(value).__name__.lower()
    if class_name in {"curve", "object"}:
        return _named_selection(class_name, getattr(value, "name", None))

    objects = getattr(layout, "objects", {})
    if scope is not None and scope["kind"] == "object":
        scoped_name = scope["name"]
        object_items = (
            [(scoped_name, objects[scoped_name])] if scoped_name in objects else []
        )
    elif scope is not None and scope["kind"] == "curve":
        object_items = []
    else:
        object_items = list(objects.items())
    if class_name == "type":
        matches = [
            object_name
            for object_name, object_value in object_items
            if getattr(object_value, "type", None) is value
        ]
        if len(matches) == 1:
            return _named_selection("object", matches[0])
        if matches:
            raise ValueError(
                "type selection is ambiguous across multiple objects; select an "
                "object explicitly"
            )
    if class_name == "frame":
        owner = getattr(value, "owner", None)
        frame_name = getattr(value, "name", None)
        matches = [
            object_name
            for object_name, object_value in object_items
            if getattr(object_value, "type", None) is owner
        ]
        if len(matches) == 1:
            return _frame_target(matches[0], frame_name)
        if matches:
            raise ValueError(
                "frame selection is ambiguous across multiple objects; use "
                "'object->frame'"
            )
    raise TypeError("target must identify a curve, object, type, or object frame")


def _checked_selection_target(
    value: object,
    layout: object,
    scope: Mapping[str, str] | None = None,
) -> dict[str, object]:
    selection = _selection_target(value, layout, scope)
    kind = selection["kind"]
    if kind == "curve":
        name = cast(str, selection["name"])
        curves = getattr(layout, "curves", {})
        if name not in curves:
            raise KeyError(f"unknown curve {name!r}")
        attached = curves[name]
        if type(value).__name__.lower() == "curve" and attached is not value:
            raise ValueError("selected curve does not belong to this layout")
        segment_index = selection.get("segmentIndex")
        if segment_index is not None and segment_index >= len(attached.segments):
            raise ValueError(f"curve {name!r} has no segment {segment_index}")
        return selection

    object_name = cast(
        str, selection["name"] if kind == "object" else selection["object"]
    )
    objects = getattr(layout, "objects", {})
    if object_name not in objects:
        raise KeyError(f"unknown object {object_name!r}")
    attached_object = objects[object_name]
    if kind == "object":
        if type(value).__name__.lower() == "object" and attached_object is not value:
            raise ValueError("selected object does not belong to this layout")
        return selection

    frame_name = cast(str, selection["name"])
    type_name = getattr(attached_object, "type_name", None)
    types = getattr(layout, "types", {})
    if type_name not in types:
        raise KeyError(f"object {object_name!r} has unknown type {type_name!r}")
    attached_type = types[type_name]
    implicit = _implicit_object_frames(attached_type, attached_object)
    if frame_name not in implicit and frame_name not in attached_type.frames:
        raise KeyError(f"object {object_name!r} has no frame {frame_name!r}")
    return selection


def _selection_in_scope(
    selection: Mapping[str, object], scope: Mapping[str, str]
) -> bool:
    if scope["kind"] == "layout":
        return True
    if scope["kind"] == "curve":
        return (
            selection.get("kind") == "curve" and selection.get("name") == scope["name"]
        )
    if selection.get("kind") == "object":
        return selection.get("name") == scope["name"]
    return selection.get("kind") == "frame" and selection.get("object") == scope["name"]


def _fit_in_scope(target: Mapping[str, str], scope: Mapping[str, str]) -> bool:
    return (
        scope["kind"] == "layout"
        or target["kind"] == "layout"
        or (target["kind"] == scope["kind"] and target.get("name") == scope.get("name"))
    )


def _visibility_values(
    value: Mapping[str, object] | None,
    *,
    require_value: bool = False,
) -> dict[str, bool]:
    if value is None:
        if require_value:
            raise TypeError("set_visibility requires at least one layer value")
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("visibility must be a mapping")
    allowed = {"curves", "objects", "magnetic_axis", "beam_axis", "frames"}
    supplied: dict[str, bool] = {}
    for key, item in value.items():
        if key not in allowed:
            raise TypeError(f"unknown visibility layer {key!r}")
        # Public set_visibility uses None to mean that a keyword was omitted.
        if require_value and item is None:
            continue
        if not isinstance(item, bool):
            raise TypeError("visibility values must be bool")
        supplied[cast(str, key)] = item
    if require_value and not supplied:
        raise TypeError("set_visibility requires at least one layer value")
    return supplied


def _named_selection(kind: object, name: object) -> dict[str, str]:
    if kind not in {"curve", "object"}:
        raise TypeError("selection kind must be 'curve', 'object', or 'frame'")
    if not isinstance(name, str) or not name:
        raise ValueError("selection must have a non-empty attached name")
    return {"kind": cast(str, kind), "name": name}


def _frame_target(object_name: object, frame_name: object) -> dict[str, str]:
    if not isinstance(object_name, str) or not object_name:
        raise ValueError("frame selection must have a non-empty object name")
    if not isinstance(frame_name, str) or not frame_name:
        raise ValueError("frame selection must have a non-empty frame name")
    return {"kind": "frame", "object": object_name, "name": frame_name}


def _frame_target_from_text(text: str) -> dict[str, str]:
    separator = "->" if "->" in text else "."
    object_name, frame_name = text.split(separator, 1)
    return _frame_target(object_name, frame_name)


def _timeout_value(
    value: float | None,
    label: str,
    *,
    allow_none: bool = False,
) -> float | None:
    if value is None:
        if allow_none:
            return None
        raise TypeError(f"{label} must be a finite non-negative number")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a finite non-negative number or None")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{label} must be finite and non-negative")
    return result


__all__ = [
    "WebViewer",
    "WebViewerAssetError",
    "WebViewerError",
    "WebViewerTimeoutError",
]
