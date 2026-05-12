"""MedlinePlus drug-handout URL resolver.

Returns a deterministic URL the LLM can cite for any patient-facing drug
claim (R4 mechanical). For the demo drug-set we ship a curated mapping
keyed by RxCUI — that's the highest-confidence answer. For unknown drugs
we fall back to a MedlinePlus search URL, which is still authoritative but
less specific. The LLM never composes URLs.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from pydantic import HttpUrl

from medrec_superpower.schemas import StrictModel

# RxCUI → direct MedlinePlus drug-info page. Curated for the P123 demo
# scenario + common cardiometabolic drugs. Extend as needed; the search
# fallback ensures we always return *something* authoritative.
_RXCUI_TO_URL: dict[str, str] = {
    "860975": "https://medlineplus.gov/druginfo/meds/a696005.html",  # Metformin
    "314076": "https://medlineplus.gov/druginfo/meds/a692051.html",  # Lisinopril
    "200316": "https://medlineplus.gov/druginfo/meds/a695008.html",  # Losartan
    "617310": "https://medlineplus.gov/druginfo/meds/a600045.html",  # Atorvastatin
    "11289": "https://medlineplus.gov/druginfo/meds/a682277.html",  # Warfarin
    "5640": "https://medlineplus.gov/druginfo/meds/a682159.html",  # Ibuprofen
}


class DrugHandout(StrictModel):
    """A patient-facing drug education citation, suitable for R4 use."""

    rxcui: str | None = None
    display: str
    url: HttpUrl
    source: str = "medlineplus.gov"
    exact_match: bool


def resolve(rxcui: str | None, display: str) -> DrugHandout:
    """Return a :class:`DrugHandout` for ``rxcui`` (preferred) or ``display``.

    When the RxCUI is in our curated map we return the canonical drug page
    with ``exact_match=True``. Otherwise we build a MedlinePlus search URL
    using ``display`` and mark ``exact_match=False`` so the caller can warn
    the user that the link is a search hit, not a confirmed drug page.
    """
    if rxcui and rxcui in _RXCUI_TO_URL:
        return DrugHandout(
            rxcui=rxcui,
            display=display,
            url=_RXCUI_TO_URL[rxcui],  # type: ignore[arg-type]
            exact_match=True,
        )
    query = display.strip() or "drug information"
    search_url = "https://medlineplus.gov/search/?query=" + quote_plus(query)
    return DrugHandout(
        rxcui=rxcui,
        display=display,
        url=search_url,  # type: ignore[arg-type]
        exact_match=False,
    )


__all__ = ["DrugHandout", "resolve"]
