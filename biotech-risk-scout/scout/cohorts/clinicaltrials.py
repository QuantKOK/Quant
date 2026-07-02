"""Standard-library ClinicalTrials.gov API v2 client.

Reference: https://clinicaltrials.gov/data-api/api

The client provides:

* ``get_version()`` -> the API version payload (retains ``dataTimestamp``)
* ``iter_studies(params)`` -> paginated study iteration via ``nextPageToken``
* ``fetch_full_study(nct_id)`` -> one authoritative full-study response

It uses an explicit timeout, bounded retries for transient failures, and a
descriptive ``User-Agent`` read from the ``CTG_USER_AGENT`` environment variable.
Network and malformed-response conditions raise clear, typed errors. No study
data, URL, date, result, or label is ever fabricated by this module.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterator

API_BASE = "https://clinicaltrials.gov/api/v2"
STUDIES_URL = f"{API_BASE}/studies"
VERSION_URL = f"{API_BASE}/version"

USER_AGENT_ENV = "CTG_USER_AGENT"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_PAGE_SIZE = 50
MAX_PAGES = 200  # hard safety bound on pagination

#: HTTP statuses worth retrying (rate limiting and transient server errors).
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

#: Transport signature: (url, headers, timeout) -> (status_code, body_bytes).
Transport = Callable[[str, dict[str, str], int], "tuple[int, bytes]"]


class ClinicalTrialsError(RuntimeError):
    """Base error for the ClinicalTrials.gov client."""


class MissingUserAgentError(ClinicalTrialsError):
    """Raised when the CTG_USER_AGENT environment variable is not configured."""


class ClinicalTrialsNetworkError(ClinicalTrialsError):
    """Raised when the API cannot be reached after bounded retries."""


class ClinicalTrialsResponseError(ClinicalTrialsError):
    """Raised on a non-retryable HTTP status or a malformed/invalid response."""


class TransientTransportError(Exception):
    """Raised by a transport for a retryable network condition (timeout, DNS...)."""


def resolve_user_agent(explicit: str | None = None) -> str:
    """Return a descriptive User-Agent or raise ``MissingUserAgentError``.

    The URL/secret is never logged. A descriptive User-Agent is required by the
    ClinicalTrials.gov API terms for scripted access.
    """
    candidate = explicit if explicit is not None else os.environ.get(USER_AGENT_ENV, "")
    candidate = (candidate or "").strip()
    if not candidate:
        raise MissingUserAgentError(
            f"{USER_AGENT_ENV} must be set to a descriptive User-Agent "
            "(e.g. 'BiotechRiskScout research (you@example.com)') before calling the API."
        )
    return candidate


def _default_transport(url: str, headers: dict[str, str], timeout: int) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            return int(status), response.read()
    except urllib.error.HTTPError as exc:  # server returned a status code
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return int(exc.code), body
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise TransientTransportError(str(getattr(exc, "reason", exc))) from exc


class ClinicalTrialsClient:
    """A small, deterministic ClinicalTrials.gov API v2 client."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        backoff_base: float = 0.5,
    ) -> None:
        self.user_agent = resolve_user_agent(user_agent)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
            raise ValueError("timeout must be a positive integer")
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 1:
            raise ValueError("max_retries must be a positive integer")
        self.timeout = timeout
        self.max_retries = max_retries
        self._transport = transport or _default_transport
        self._sleep = sleep
        self._backoff_base = backoff_base

    # -- HTTP ---------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "User-Agent": self.user_agent}

    def _get_bytes(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                status, body = self._transport(url, self._headers(), self.timeout)
            except TransientTransportError as exc:
                last_error = ClinicalTrialsNetworkError(f"network error contacting the API: {exc}")
                if attempt < self.max_retries:
                    self._sleep(self._backoff_base * attempt)
                    continue
                raise last_error from exc
            if status in RETRYABLE_STATUS:
                last_error = ClinicalTrialsNetworkError(
                    f"transient HTTP {status} from the API after {attempt} attempt(s)"
                )
                if attempt < self.max_retries:
                    self._sleep(self._backoff_base * attempt)
                    continue
                raise last_error
            if status != 200:
                raise ClinicalTrialsResponseError(f"unexpected HTTP {status} from the API")
            return body
        raise last_error or ClinicalTrialsNetworkError("request failed")

    def _get_json(self, url: str) -> dict[str, Any]:
        body = self._get_bytes(url)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ClinicalTrialsResponseError(f"malformed JSON response from the API: {exc}") from exc
        if not isinstance(payload, dict):
            raise ClinicalTrialsResponseError("API response was not a JSON object")
        return payload

    # -- Endpoints ----------------------------------------------------------

    def get_version(self) -> dict[str, Any]:
        """Return the API version payload, including ``dataTimestamp``."""
        payload = self._get_json(VERSION_URL)
        if "dataTimestamp" not in payload:
            raise ClinicalTrialsResponseError("version response missing dataTimestamp")
        return payload

    def iter_studies(self, params: dict[str, Any], max_pages: int | None = None) -> Iterator[dict[str, Any]]:
        """Yield studies for a search query, following ``nextPageToken``.

        ``max_pages`` bounds how many pages are fetched (in addition to the
        hard ``MAX_PAGES`` safety limit); ``None`` uses the safety limit.
        """
        if max_pages is not None and (
            not isinstance(max_pages, int)
            or isinstance(max_pages, bool)
            or max_pages < 1
        ):
            raise ValueError("max_pages must be a positive integer or None")
        if max_pages is not None and max_pages > MAX_PAGES:
            raise ValueError(f"max_pages cannot exceed the hard limit of {MAX_PAGES}")
        page_cap = MAX_PAGES if max_pages is None else max_pages
        query = dict(params)
        pages = 0
        while True:
            url = f"{STUDIES_URL}?{urllib.parse.urlencode(query)}"
            payload = self._get_json(url)
            studies = payload.get("studies")
            if not isinstance(studies, list):
                raise ClinicalTrialsResponseError("studies response missing 'studies' list")
            for study in studies:
                if not isinstance(study, dict):
                    raise ClinicalTrialsResponseError("study entry was not a JSON object")
                yield study

            token = payload.get("nextPageToken")
            pages += 1
            if not token or not studies:
                break
            if pages >= page_cap:
                if max_pages is None:
                    raise ClinicalTrialsResponseError(
                        f"study search exceeded the hard pagination limit of {MAX_PAGES} pages"
                    )
                break
            query["pageToken"] = token

    def fetch_full_study(self, nct_id: str) -> dict[str, Any]:
        """Fetch one authoritative full-study response for ``nct_id``."""
        url = f"{STUDIES_URL}/{urllib.parse.quote(nct_id)}?format=json"
        payload = self._get_json(url)
        # v2 returns the study object directly; guard against surprises.
        if "protocolSection" not in payload:
            raise ClinicalTrialsResponseError(
                f"full-study response for {nct_id} missing protocolSection"
            )
        return payload
