"""Watchdog for monitoring and inspecting network traffic (XHR/Fetch)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from bubus import BaseEvent
from pydantic import PrivateAttr

from browser_use.browser.events import BrowserStateRequestEvent, TabCreatedEvent
from browser_use.browser.watchdog_base import BaseWatchdog

if TYPE_CHECKING:
        from cdp_use.cdp.network import (
                LoadingFailedEvent,
                LoadingFinishedEvent,
                RequestWillBeSentEvent,
                ResponseReceivedEvent,
        )
        from cdp_use.cdp.target import SessionID, TargetID


@dataclass
class NetworkLogEntry:
        """Represents a single network request/response cycle."""

        request_id: str
        url: str
        method: str
        resource_type: str
        start_time: float
        status: int | None = None
        status_text: str | None = None
        mime_type: str | None = None
        error_text: str | None = None
        initiator_type: str | None = None
        request_headers: dict[str, str] = field(default_factory=dict)
        response_headers: dict[str, str] = field(default_factory=dict)
        request_body_preview: str | None = None
        encoded_data_length: float | None = None
        end_time: float | None = None
        stack_trace: list[dict[str, Any]] = field(default_factory=list)

        def to_string(self) -> str:
                """Compact string representation for LLM consumption."""

                status_str = str(self.status) if self.status else (f'FAILED({self.error_text})' if self.error_text else 'PENDING')
                duration = f" {self.duration_ms:.0f}ms" if self.duration_ms is not None else ''
                return f'[{self.method}] {status_str} {self.url} ({self.resource_type}){duration}'

        @property
        def duration_ms(self) -> float | None:
                if self.end_time is None:
                        return None
                return max(0.0, (self.end_time - self.start_time) * 1000.0)

        def has_stack(self) -> bool:
                return bool(self.stack_trace)


class NetworkWatchdog(BaseWatchdog):
        """Monitors network traffic to allow agents to inspect API calls and data."""

        LISTENS_TO: ClassVar[list[type[BaseEvent[Any]]]] = [TabCreatedEvent, BrowserStateRequestEvent]
        EMITS: ClassVar[list[type[BaseEvent[Any]]]] = []

        max_logs_per_tab: int = 200
        ignored_resource_types: set[str] = {'Image', 'Stylesheet', 'Font', 'Media', 'Manifest', 'Other'}

        _network_logs: dict[str, deque[NetworkLogEntry]] = PrivateAttr(default_factory=dict)
        _monitored_targets: set[str] = PrivateAttr(default_factory=set)

        async def on_TabCreatedEvent(self, event: TabCreatedEvent) -> None:
                if event.target_id:
                        await self.attach_to_target(event.target_id)

        async def on_BrowserStateRequestEvent(self, event: BrowserStateRequestEvent) -> None:
                if self.browser_session.agent_focus_target_id:
                        await self.attach_to_target(self.browser_session.agent_focus_target_id)

        async def attach_to_target(self, target_id: 'TargetID') -> None:
                if target_id in self._monitored_targets:
                        return

                try:
                        session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)

                        if target_id not in self._network_logs:
                                self._network_logs[target_id] = deque(maxlen=self.max_logs_per_tab)

                        await session.cdp_client.send.Network.enable(session_id=session.session_id)

                        def on_request(event: 'RequestWillBeSentEvent', _sid: 'SessionID | None') -> None:
                                resource_type = event.get('type', 'Unknown')
                                if resource_type in self.ignored_resource_types:
                                        return

                                request = event.get('request', {})
                                request_headers = {k: str(v) for k, v in request.get('headers', {}).items()}
                                post_data = request.get('postData')
                                request_body_preview = None
                                if isinstance(post_data, str):
                                        request_body_preview = post_data if len(post_data) <= 2000 else f"{post_data[:2000]}... (truncated {len(post_data) - 2000} chars)"

                                stack_frames = self._extract_initiator_frames(event.get('initiator'))

                                entry = NetworkLogEntry(
                                        request_id=event['requestId'],
                                        url=request.get('url', ''),
                                        method=request.get('method', 'GET'),
                                        resource_type=resource_type,
                                        start_time=event.get('wallTime', 0.0),
                                        initiator_type=event.get('initiator', {}).get('type') if isinstance(event.get('initiator'), dict) else None,
                                        request_headers=request_headers,
                                        request_body_preview=request_body_preview,
                                        stack_trace=stack_frames,
                                )
                                self._network_logs[target_id].append(entry)

                        def on_response(event: 'ResponseReceivedEvent', _sid: 'SessionID | None') -> None:
                                req_id = event['requestId']
                                response = event.get('response', {})

                                entry = self._find_entry(target_id, req_id)
                                if entry is None:
                                        return

                                entry.status = response.get('status')
                                entry.status_text = response.get('statusText')
                                entry.mime_type = response.get('mimeType')
                                if 'headers' in response:
                                        entry.response_headers = {k: str(v) for k, v in response['headers'].items()}
                                entry.end_time = response.get('responseTime') or entry.end_time

                        def on_failure(event: 'LoadingFailedEvent', _sid: 'SessionID | None') -> None:
                                req_id = event['requestId']
                                entry = self._find_entry(target_id, req_id)
                                if entry is None:
                                        return

                                entry.error_text = event.get('errorText')
                                if entry.status is None:
                                        entry.status = 0
                                if entry.end_time is None:
                                        entry.end_time = event.get('timestamp')

                        def on_loading_finished(event: 'LoadingFinishedEvent', _sid: 'SessionID | None') -> None:
                                req_id = event['requestId']
                                entry = self._find_entry(target_id, req_id)
                                if entry is None:
                                        return

                                entry.encoded_data_length = event.get('encodedDataLength')
                                entry.end_time = event.get('timestamp')

                        session.cdp_client.register.Network.requestWillBeSent(on_request)
                        session.cdp_client.register.Network.responseReceived(on_response)
                        session.cdp_client.register.Network.loadingFailed(on_failure)
                        session.cdp_client.register.Network.loadingFinished(on_loading_finished)

                        self._monitored_targets.add(target_id)
                        self.logger.debug(f'[NetworkWatchdog] Attached to target {target_id[-4:]}')

                except Exception as e:
                        self.logger.warning(f'[NetworkWatchdog] Failed to attach to {target_id[-4:]}: {e}')

        def get_traffic_log(self, target_id: 'TargetID') -> list[NetworkLogEntry]:
                if target_id not in self._network_logs:
                        return []
                return list(self._network_logs[target_id])

        def find_entry(self, target_id: 'TargetID', url_pattern: str) -> NetworkLogEntry | None:
                logs = self.get_traffic_log(target_id)
                url_pattern_lower = url_pattern.lower()
                for entry in reversed(logs):
                        if url_pattern_lower in entry.url.lower():
                                return entry
                return None

        async def get_response_body(self, target_id: 'TargetID', request_id: str) -> str | None:
                try:
                        session = await self.browser_session.get_or_create_cdp_session(target_id, focus=False)

                        result = await session.cdp_client.send.Network.getResponseBody(
                                params={'requestId': request_id}, session_id=session.session_id
                        )

                        body = result.get('body')
                        if body is None:
                                return None

                        if result.get('base64Encoded'):
                                import base64

                                try:
                                        decoded = base64.b64decode(body)
                                        return decoded.decode('utf-8', errors='replace')
                                except Exception:
                                        return '<base64-encoded response body could not be decoded>'

                        return body
                except Exception as e:
                        self.logger.debug(f'[NetworkWatchdog] Failed to get body for {request_id}: {e}')
                        return None

        async def format_entry_details(
                self,
                target_id: 'TargetID',
                entry: NetworkLogEntry,
                include_headers: bool = True,
                include_request_body: bool = True,
                include_response_body: bool = False,
                body_length: int = 4000,
        ) -> str:
                lines = [
                        f"Request: [{entry.method}] {entry.url}",
                        f"Type: {entry.resource_type}",
                ]

                if entry.status is not None:
                        status_label = f"Status: {entry.status}"
                        if entry.status_text:
                                status_label += f" ({entry.status_text})"
                        lines.append(status_label)
                if entry.error_text:
                        lines.append(f"Error: {entry.error_text}")
                if entry.mime_type:
                        lines.append(f"MIME: {entry.mime_type}")
                if entry.duration_ms is not None:
                        lines.append(f"Duration: {entry.duration_ms:.0f} ms")
                if entry.encoded_data_length is not None:
                        lines.append(f"Size: {entry.encoded_data_length:.0f} bytes")
                if entry.initiator_type:
                        lines.append(f"Initiator: {entry.initiator_type}")

                if include_headers and entry.request_headers:
                        header_lines = '\n'.join([f"    {k}: {v}" for k, v in entry.request_headers.items()])
                        lines.append(f"Request Headers:\n{header_lines}")
                if include_request_body and entry.request_body_preview:
                        lines.append(f"Request Body (preview):\n{entry.request_body_preview}")
                if include_headers and entry.response_headers:
                        resp_header_lines = '\n'.join([f"    {k}: {v}" for k, v in entry.response_headers.items()])
                        lines.append(f"Response Headers:\n{resp_header_lines}")

                if include_response_body:
                        body = await self.get_response_body(target_id, entry.request_id)
                        if body:
                                if len(body) > body_length:
                                        body = f"{body[:body_length]}\n... (truncated {len(body) - body_length} chars)"
                                lines.append(f"Response Body:\n{body}")
                        else:
                                lines.append('Response Body: <not available>')

                return '\n'.join(lines)

        def format_stack_trace(self, entry: NetworkLogEntry, frame_limit: int = 10) -> str:
                if not entry.stack_trace:
                        return ''

                lines = []
                for idx, frame in enumerate(entry.stack_trace[:frame_limit]):
                        function_name = frame.get('functionName') or '(anonymous)'
                        url = frame.get('url') or '<unknown>'
                        line = frame.get('lineNumber', 0) + 1 if isinstance(frame.get('lineNumber'), (int, float)) else '?'
                        column = frame.get('columnNumber', 0) + 1 if isinstance(frame.get('columnNumber'), (int, float)) else '?'
                        lines.append(f"#{idx} {function_name} @ {url}:{line}:{column}")

                return '\n'.join(lines)

        def _find_entry(self, target_id: 'TargetID', request_id: str) -> NetworkLogEntry | None:
                logs = self._network_logs.get(target_id)
                if not logs:
                        return None

                for entry in reversed(logs):
                        if entry.request_id == request_id:
                                return entry
                return None

        def _extract_initiator_frames(self, initiator: Any) -> list[dict[str, Any]]:
                if not isinstance(initiator, dict):
                        return []

                frames: list[dict[str, Any]] = []
                stack = initiator.get('stack') or initiator.get('stackTrace')

                while stack:
                        call_frames = stack.get('callFrames') or []
                        frames.extend(call_frames)
                        stack = stack.get('parent') if isinstance(stack, dict) else None

                return frames
