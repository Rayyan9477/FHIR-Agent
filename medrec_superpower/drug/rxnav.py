"""Async RxNav client — the *only* place we speak RxNav HTTP.

R3 mechanical: every error path (5xx after retries, timeout, malformed JSON,
4xx including the documented post-2024 deprecation of ``/interaction/list``)
returns ``InteractionResult(check_succeeded=False, error_message=...)``.
The MCP tool layer then surfaces this to the Coordinator, which must tell
the user explicitly — never substitute LLM knowledge.
"""

from __future__ import annotations

import json
from types import TracebackType
from typing import cast

import httpx
import structlog
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from typing_extensions import Self

from medrec_superpower.drug.schemas import (
    InteractionRecord,
    InteractionResult,
    InteractionSeverity,
    RxNormMatch,
)

logger = structlog.get_logger(__name__)

_RXNAV_BASE_URL = "https://rxnav.nlm.nih.gov/REST"
_INTERACTION_PATH = "/interaction/list.json"
_APPROX_TERM_PATH = "/approximateTerm.json"
_DEFAULT_TIMEOUT_S = 5.0
_DEFAULT_CONNECT_TIMEOUT_S = 2.0
_MAX_ATTEMPTS = 3

_RETRYABLE: tuple[type[BaseException], ...] = (
    httpx.HTTPStatusError,
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


class RxNavClient:
    """Async context-managed RxNav client.

    Usage::

        async with RxNavClient() as rx:
            result = await rx.check_interaction("11289", "5640")
            if not result.check_succeeded:
                ...  # R3: surface to user, never hallucinate
    """

    def __init__(
        self,
        *,
        base_url: str = _RXNAV_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_S,
        connect_timeout_seconds: float = _DEFAULT_CONNECT_TIMEOUT_S,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> None:
        self._base_url = base_url
        self._timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
        self._max_attempts = max_attempts
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def lookup_rxnorm(self, term: str, *, max_results: int = 5) -> list[RxNormMatch]:
        """Look up RxCUIs for a free-text drug term via RxNav ``approximateTerm``.

        Returns a ranked list of candidates (best first). Empty list on failure
        — never raises across the public surface. The caller decides whether
        to treat an empty result as "unknown drug" or retry.
        """
        if self._client is None:
            raise RuntimeError("RxNavClient must be used as an async context manager")
        cleaned = term.strip()
        if not cleaned:
            return []
        params = {"term": cleaned, "maxEntries": str(max(1, min(50, max_results)))}
        try:
            response = await self._client.get(_APPROX_TERM_PATH, params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "rxnav.lookup_rxnorm.transport_error",
                term=cleaned,
                error=str(exc),
            )
            return []
        if response.status_code != 200:
            logger.warning(
                "rxnav.lookup_rxnorm.upstream_error",
                term=cleaned,
                status=response.status_code,
            )
            return []
        try:
            payload: object = response.json()
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []
        group = payload.get("approximateGroup")
        if not isinstance(group, dict):
            return []
        candidates = group.get("candidate")
        out: list[RxNormMatch] = []
        if isinstance(candidates, list):
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                rxcui = c.get("rxcui")
                if not isinstance(rxcui, str) or not rxcui:
                    continue
                display = c.get("name") if isinstance(c.get("name"), str) else cleaned
                score_raw = c.get("score")
                try:
                    score = float(score_raw) if score_raw is not None else 0.0
                except (TypeError, ValueError):
                    score = 0.0
                tty = c.get("rxcuiType") if isinstance(c.get("rxcuiType"), str) else None
                out.append(
                    RxNormMatch(
                        rxcui=rxcui,
                        display=display or cleaned,
                        score=score,
                        term_type=tty,
                    )
                )
        return out

    async def check_interaction(self, rxcui_a: str, rxcui_b: str) -> InteractionResult:
        """Look up drug-drug interactions between two RxCUIs.

        Never raises. All failure modes collapse into ``check_succeeded=False``.
        """
        if self._client is None:
            raise RuntimeError("RxNavClient must be used as an async context manager")
        if not rxcui_a or not rxcui_b:
            return self._failed(rxcui_a, rxcui_b, "rxcui values must be non-empty")

        payload, fail_reason = await self._fetch(rxcui_a, rxcui_b)
        if payload is None:
            return self._failed(rxcui_a, rxcui_b, fail_reason or "unknown failure")

        try:
            return _parse_interaction_payload(rxcui_a, rxcui_b, payload)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            logger.warning(
                "rxnav.check_interaction.malformed",
                rxcui_a=rxcui_a,
                rxcui_b=rxcui_b,
                error=str(exc),
            )
            return self._failed(rxcui_a, rxcui_b, f"RxNav response malformed: {exc}")

    async def _fetch(
        self, rxcui_a: str, rxcui_b: str
    ) -> tuple[dict[str, object] | None, str | None]:
        """Returns (payload, None) on success, (None, reason) on failure."""
        if self._client is None:
            # Invariant: `check_interaction` already validates this. The check
            # here is defensive against future call-site bugs.
            raise RuntimeError("RxNavClient not in async-context state")
        url = _INTERACTION_PATH
        params: dict[str, str] = {"rxcuis": f"{rxcui_a}+{rxcui_b}"}

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential(multiplier=0.25, max=2.0),
                retry=retry_if_exception_type(_RETRYABLE),
                reraise=False,
            ):
                with attempt:
                    response = await self._client.get(url, params=params)
                    if 500 <= response.status_code < 600:
                        raise httpx.HTTPStatusError(
                            f"upstream HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    if response.status_code == 404:
                        # /interaction/list deprecated 2024-01 — non-retryable
                        return None, (
                            "RxNav /interaction/list returned 404 "
                            "(endpoint deprecated 2024-01; openFDA fallback is P2)"
                        )
                    if 400 <= response.status_code < 500:
                        return None, f"RxNav rejected request: HTTP {response.status_code}"
                    try:
                        payload_any: object = response.json()
                    except json.JSONDecodeError as exc:
                        return None, f"RxNav response not JSON: {exc}"
                    if not isinstance(payload_any, dict):
                        return None, "RxNav response was not a JSON object"
                    logger.info(
                        "rxnav.check_interaction.success",
                        rxcui_a=rxcui_a,
                        rxcui_b=rxcui_b,
                        status=response.status_code,
                    )
                    return cast("dict[str, object]", payload_any), None
        except RetryError as exc:
            last_exc = exc.last_attempt.exception() if exc.last_attempt else exc
            logger.warning(
                "rxnav.check_interaction.upstream_failed",
                rxcui_a=rxcui_a,
                rxcui_b=rxcui_b,
                error=str(last_exc),
            )
            return None, f"RxNav unavailable after {self._max_attempts} attempts: {last_exc}"
        # Unreachable: the for-loop always returns or raises.
        return None, "unreachable"  # pragma: no cover

    @staticmethod
    def _failed(rxcui_a: str, rxcui_b: str, reason: str) -> InteractionResult:
        return InteractionResult(
            rxcui_a=rxcui_a,
            rxcui_b=rxcui_b,
            check_succeeded=False,
            error_message=reason,
        )


def _normalize_severity(raw: object) -> InteractionSeverity:
    if not isinstance(raw, str):
        return "unknown"
    lowered = raw.strip().lower()
    if lowered in ("high", "moderate", "low"):
        return cast("InteractionSeverity", lowered)
    return "unknown"


def _as_list(value: object) -> list[object]:
    """Return ``value`` if it's a list, else an empty list — for defensive walks."""
    return value if isinstance(value, list) else []


def _as_dict(value: object) -> dict[str, object]:
    """Return ``value`` if it's a dict[str, object], else an empty dict."""
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if isinstance(k, str)}
    return {}


def _extract_citations(pair: dict[str, object]) -> list[str]:
    citations: list[str] = []
    for concept in _as_list(pair.get("interactionConcept")):
        src = _as_dict(_as_dict(concept).get("sourceConceptItem"))
        url = src.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            citations.append(url)
    return citations


def _parse_interaction_payload(
    rxcui_a: str, rxcui_b: str, payload: dict[str, object]
) -> InteractionResult:
    """Map RxNav ``/interaction/list`` JSON to our typed result.

    Defensive: every nested access tolerates type drift in the upstream
    payload. Unknown shapes degrade to "no interactions found" rather than
    crashing — RxNav has historically returned varying envelopes.
    """
    groups = _as_list(payload.get("fullInteractionTypeGroup"))
    if not groups:
        return InteractionResult(rxcui_a=rxcui_a, rxcui_b=rxcui_b, check_succeeded=True)

    records: list[InteractionRecord] = []
    for group_obj in groups:
        group = _as_dict(group_obj)
        for it_obj in _as_list(group.get("fullInteractionType")):
            it = _as_dict(it_obj)
            for pair_obj in _as_list(it.get("interactionPair")):
                pair = _as_dict(pair_obj)
                description_raw = pair.get("description")
                if not isinstance(description_raw, str) or not description_raw.strip():
                    continue
                severity = _normalize_severity(pair.get("severity"))
                citations = _extract_citations(pair)
                records.append(
                    InteractionRecord(
                        severity=severity,
                        description=description_raw.strip(),
                        citations=citations,  # type: ignore[arg-type]
                    )
                )
    return InteractionResult(
        rxcui_a=rxcui_a,
        rxcui_b=rxcui_b,
        check_succeeded=True,
        interactions=records,
    )


__all__ = ["RxNavClient"]
