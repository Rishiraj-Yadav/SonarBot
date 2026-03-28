"""Site-specific browser adapter metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserSiteAdapter:
    canonical_name: str
    aliases: tuple[str, ...] = ()
    search_input_selectors: tuple[str, ...] = ()
    search_expand_selectors: tuple[str, ...] = ()
    result_strategy: str = "generic"


SITE_ADAPTERS: dict[str, BrowserSiteAdapter] = {
    "youtube": BrowserSiteAdapter(
        canonical_name="youtube",
        aliases=("youtube.com", "www.youtube.com", "yt"),
        search_input_selectors=(
            "input[name='search_query']",
            "input#search",
            "ytd-searchbox input",
            "input[placeholder*='Search' i]",
        ),
        search_expand_selectors=(
            "button[aria-label='Search']",
            "button[title='Search']",
        ),
        result_strategy="youtube",
    ),
    "google": BrowserSiteAdapter(
        canonical_name="google",
        aliases=("google.com", "www.google.com"),
        search_input_selectors=(
            "textarea[name='q']",
            "input[name='q']",
            "textarea[aria-label*='Search' i]",
            "input[aria-label*='Search' i]",
        ),
        result_strategy="google",
    ),
    "github": BrowserSiteAdapter(
        canonical_name="github",
        aliases=("github.com", "www.github.com"),
        search_input_selectors=(
            "input[aria-label*='Search or jump to' i]",
            "input[placeholder*='Search or jump to' i]",
            "qbsearch-input input",
            "input[data-target*='query-builder']",
            "input[name='q']",
        ),
        search_expand_selectors=(
            "button[aria-label*='Search or jump to' i]",
            "button[data-target='qbsearch-input.inputButton']",
            "button[title='Search']",
        ),
        result_strategy="github",
    ),
    "leetcode": BrowserSiteAdapter(
        canonical_name="leetcode",
        aliases=("leetcode.com", "www.leetcode.com", "leet code"),
        search_input_selectors=(
            "input[placeholder*='Search questions' i]",
            "input[placeholder*='Search' i]",
            "input[data-cy='quick-search-input']",
            "input[name='search']",
        ),
        result_strategy="leetcode",
    ),
}


def get_site_adapter(site_name: str | None) -> BrowserSiteAdapter | None:
    if not site_name:
        return None
    lowered = site_name.strip().lower()
    for adapter in SITE_ADAPTERS.values():
        if lowered == adapter.canonical_name or lowered in adapter.aliases:
            return adapter
        if lowered.endswith(f".{adapter.canonical_name}") or adapter.canonical_name in lowered:
            return adapter
        if any(alias in lowered for alias in adapter.aliases):
            return adapter
    return None
