from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


# Action Input Models
class ExtractAction(BaseModel):
	query: str
	extract_links: bool = Field(
		default=False, description='Set True to true if the query requires links, else false to safe tokens'
	)
	start_from_char: int = Field(
		default=0, description='Use this for long markdowns to start from a specific character (not index in browser_state)'
	)


class SearchAction(BaseModel):
	query: str
	engine: str = Field(
		default='duckduckgo', description='duckduckgo, google, bing (use duckduckgo by default because less captchas)'
	)


# Backward compatibility alias
SearchAction = SearchAction


class NavigateAction(BaseModel):
	url: str
	new_tab: bool = Field(default=False)


# Backward compatibility alias
GoToUrlAction = NavigateAction


class ClickElementAction(BaseModel):
	index: int | None = Field(default=None, ge=1, description='Element index from browser_state')
	coordinate_x: int | None = Field(default=None, description='Horizontal coordinate relative to viewport left edge')
	coordinate_y: int | None = Field(default=None, description='Vertical coordinate relative to viewport top edge')
	# expect_download: bool = Field(default=False, description='set True if expecting a download, False otherwise')  # moved to downloads_watchdog.py
	# click_count: int = 1  # TODO


class InputTextAction(BaseModel):
	index: int = Field(ge=0, description='from browser_state')
	text: str
	clear: bool = Field(default=True, description='1=clear, 0=append')


class DoneAction(BaseModel):
	text: str = Field(description='Final user message in the format the user requested')
	success: bool = Field(default=True, description='True if user_request completed successfully')
	files_to_display: list[str] | None = Field(default=[])


T = TypeVar('T', bound=BaseModel)


class StructuredOutputAction(BaseModel, Generic[T]):
	success: bool = Field(default=True, description='True if user_request completed successfully')
	data: T = Field(description='The actual output data matching the requested schema')


class SwitchTabAction(BaseModel):
	tab_id: str = Field(min_length=4, max_length=4, description='4-char id')


class CloseTabAction(BaseModel):
	tab_id: str = Field(min_length=4, max_length=4, description='4-char id')


class ScrollAction(BaseModel):
	down: bool = Field(default=True, description='down=True=scroll down, down=False scroll up')
	pages: float = Field(default=1.0, description='0.5=half page, 1=full page, 10=to bottom/top')
	index: int | None = Field(default=None, description='Optional element index to scroll within specific container')


class SendKeysAction(BaseModel):
	keys: str = Field(description='keys (Escape, Enter, PageDown) or shortcuts (Control+o)')


class UploadFileAction(BaseModel):
	index: int
	path: str


class NoParamsAction(BaseModel):
	model_config = ConfigDict(extra='ignore')


class GetDropdownOptionsAction(BaseModel):
	index: int


class SelectDropdownOptionAction(BaseModel):
	index: int
	text: str = Field(description='exact text/value')


class CheckNetworkTrafficAction(BaseModel):
	resource_type: str = Field(
		default='XHR',
		description="Filter requests by type: 'XHR', 'Fetch', 'Document', 'Script', or 'All'",
	)
	only_errors: bool = Field(
		default=False, description='If True, only returns requests that failed (status >= 400 or network error)'
	)
	limit: int = Field(default=20, ge=1, le=200, description='Maximum number of matching requests to include')


class GetResponseBodyAction(BaseModel):
	url_pattern: str = Field(..., description="Unique substring to identify the request URL (e.g. '/api/v1/search')")


class GetNetworkRequestDetailsAction(BaseModel):
	url_pattern: str = Field(..., description='Unique substring to identify the request URL to inspect')
	include_headers: bool = Field(default=True, description='Include request and response headers in the output')
	include_request_body: bool = Field(default=True, description='Include captured request body preview if available')
	include_response_body: bool = Field(default=False, description='Fetch and include the response body (truncated)')
	max_response_body_length: int = Field(default=4000, ge=500, le=20000, description='Max characters of response body')


class GetNetworkRequestStackAction(BaseModel):
	url_pattern: str = Field(..., description='Unique substring to identify the request URL whose stack trace to fetch')
	frame_limit: int = Field(default=10, ge=1, le=30, description='Maximum stack frames to include')


class DebuggerEnableAction(BaseModel):
	pause_on_exceptions: Literal['none', 'uncaught', 'all'] = Field(
		default='none', description='Exception pause mode: none, uncaught, or all'
	)


class SetBreakpointAction(BaseModel):
	url: str | None = Field(default=None, description='Script URL to match for the breakpoint')
	script_id: str | None = Field(default=None, description='Specific scriptId to target if known')
	line_number: int = Field(ge=0, description='0-based line number in the script')
	column_number: int = Field(default=0, ge=0, description='0-based column number in the script')
	condition: str | None = Field(default=None, description='Optional condition that must be true to trigger the breakpoint')


class RemoveBreakpointAction(BaseModel):
	breakpoint_id: str = Field(description='Identifier returned by set breakpoint commands')


class DebuggerPauseAction(BaseModel):
	model_config = ConfigDict(extra='ignore')


class DebuggerResumeAction(BaseModel):
	model_config = ConfigDict(extra='ignore')


class DebuggerStepAction(BaseModel):
	model_config = ConfigDict(extra='ignore')


class InspectDebuggerStateAction(BaseModel):
	max_frames: int = Field(default=5, ge=1, le=20, description='Maximum call frames to include')
	max_properties: int = Field(default=5, ge=1, le=20, description='Maximum properties to show per scope')


class EvaluatePausedFrameAction(BaseModel):
	expression: str = Field(description='JavaScript expression to evaluate in the paused frame context')
	call_frame_index: int = Field(default=0, ge=0, description='Call frame index from the paused stack to evaluate in')


class GetScriptSourceAction(BaseModel):
	script_id: str | None = Field(default=None, description='Script identifier to fetch source for')
	url: str | None = Field(default=None, description='Script URL to resolve scriptId if unknown')
	line_number: int | None = Field(
		default=None, ge=0, description='1-based line to center snippet on (defaults to pause location)'
	)
	context_lines: int = Field(default=5, ge=0, le=50, description='Lines of context to include before and after the focus line')
