"""Screenshot watchdog for handling screenshot requests using CDP."""

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

from bubus import BaseEvent
from cdp_use.cdp.page import CaptureScreenshotParameters
from pydantic import PrivateAttr

from browser_use.browser.events import ScreenshotEvent
from browser_use.browser.views import BrowserError
from browser_use.browser.watchdog_base import BaseWatchdog
from browser_use.observability import observe_debug

if TYPE_CHECKING:
        pass


class ScreenshotWatchdog(BaseWatchdog):
        """Handles screenshot requests using CDP."""

        # Events this watchdog listens to
        LISTENS_TO: ClassVar[list[type[BaseEvent[Any]]]] = [ScreenshotEvent]

        # Events this watchdog emits
        EMITS: ClassVar[list[type[BaseEvent[Any]]]] = []

        _screenshot_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

        @observe_debug(ignore_input=True, ignore_output=True, name='screenshot_event_handler')
        async def on_ScreenshotEvent(self, event: ScreenshotEvent) -> str:
                """Handle screenshot request using CDP.

		Args:
			event: ScreenshotEvent with optional full_page and clip parameters

                Returns:
                        Dict with 'screenshot' key containing base64-encoded screenshot or None
                """
                self.logger.debug('[ScreenshotWatchdog] Handler START - on_ScreenshotEvent called')
                lock_acquired = False
                timeout_seconds = event.event_timeout or 15.0
                capture_timeout = max(timeout_seconds - 2.0, 1.0)
                try:
                        try:
                                await asyncio.wait_for(self._screenshot_lock.acquire(), timeout=2.0)
                                lock_acquired = True
                        except TimeoutError:
                                raise BrowserError('[ScreenshotWatchdog] Another screenshot is already in progress')

                        # Validate focused target is a top-level page (not iframe/worker)
                        # CDP Page.captureScreenshot only works on page/tab targets
                        focused_target = self.browser_session.get_focused_target()

                        if focused_target and focused_target.target_type in ('page', 'tab'):
				target_id = focused_target.target_id
			else:
				# Focused target is iframe/worker/missing - fall back to any page target
				target_type_str = focused_target.target_type if focused_target else 'None'
				self.logger.warning(f'[ScreenshotWatchdog] Focused target is {target_type_str}, falling back to page target')
				page_targets = self.browser_session.get_page_targets()
				if not page_targets:
					raise BrowserError('[ScreenshotWatchdog] No page targets available for screenshot')
				target_id = page_targets[-1].target_id

			cdp_session = await self.browser_session.get_or_create_cdp_session(target_id, focus=True)

                        # Prepare screenshot parameters
                        params = CaptureScreenshotParameters(format='png', captureBeyondViewport=False)

                        # Take screenshot using CDP
                        self.logger.debug(f'[ScreenshotWatchdog] Taking screenshot with params: {params}')
                        result = await asyncio.wait_for(
                                cdp_session.cdp_client.send.Page.captureScreenshot(
                                        params=params, session_id=cdp_session.session_id
                                ),
                                timeout=capture_timeout,
                        )

                        # Return base64-encoded screenshot data
                        if result and 'data' in result:
                                self.logger.debug('[ScreenshotWatchdog] Screenshot captured successfully')
                                return result['data']

                        raise BrowserError('[ScreenshotWatchdog] Screenshot result missing data')
                except asyncio.TimeoutError:
                        self.logger.warning('[ScreenshotWatchdog] Screenshot capture timed out')
                        raise
                except asyncio.CancelledError:
                        self.logger.warning('[ScreenshotWatchdog] Screenshot capture was cancelled')
                        raise
                except Exception as e:
                        self.logger.error(f'[ScreenshotWatchdog] Screenshot failed: {e}')
                        raise
                finally:
                        if lock_acquired:
                                self._screenshot_lock.release()
                        # Try to remove highlights even on failure
                        try:
                                await self.browser_session.remove_highlights()
                        except Exception:
                                pass
