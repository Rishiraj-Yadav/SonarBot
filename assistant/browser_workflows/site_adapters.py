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
    # Known authenticated check URL (for session health checks)
    auth_check_url: str = ""
    # Known auto-dismissable consent / cookie selectors
    consent_accept_selectors: tuple[str, ...] = ()


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
        auth_check_url="https://www.youtube.com/feed/subscriptions",
        consent_accept_selectors=(
            "button[aria-label*='Accept all' i]",
            "button[aria-label*='Agree' i]",
            "tp-yt-paper-button[aria-label*='Accept' i]",
            "ytd-button-renderer:has-text('Accept all')",
            "button:has-text('Accept all')",
            "button:has-text('I agree')",
        ),
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
        consent_accept_selectors=(
            "button#L2AGLb",  # Google "Accept all" consent
            "button[aria-label*='Accept all' i]",
            "button:has-text('Accept all')",
            "button:has-text('I agree')",
            "div[role='none'] button:has-text('Accept')",
        ),
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
        auth_check_url="https://github.com/notifications",
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
        auth_check_url="https://leetcode.com/problems/",
    ),
    # ── new adapters ────────────────────────────────────────────────
    "twitter": BrowserSiteAdapter(
        canonical_name="twitter",
        aliases=("twitter.com", "www.twitter.com", "x.com", "www.x.com", "x", "tweet"),
        search_input_selectors=(
            "input[data-testid='SearchBox_Search_Input']",
            "input[aria-label*='Search' i]",
            "input[placeholder*='Search' i]",
        ),
        result_strategy="generic",
        consent_accept_selectors=(
            "div[data-testid='confirmationSheetConfirm']",
            "button:has-text('Accept all cookies')",
            "button:has-text('Allow all cookies')",
        ),
    ),
    "reddit": BrowserSiteAdapter(
        canonical_name="reddit",
        aliases=("reddit.com", "www.reddit.com"),
        search_input_selectors=(
            "input[placeholder*='Search Reddit' i]",
            "input[data-testid='search-input']",
            "input[name='q']",
            "input[aria-label*='Search' i]",
        ),
        result_strategy="generic",
        consent_accept_selectors=(
            "button.accept-button",
            "button:has-text('Accept all')",
            "button:has-text('Allow')",
        ),
    ),
    "amazon": BrowserSiteAdapter(
        canonical_name="amazon",
        aliases=("amazon.com", "www.amazon.com", "amazon.in", "www.amazon.in"),
        search_input_selectors=(
            "input#twotabsearchtextbox",
            "input[name='field-keywords']",
            "input[placeholder*='Search' i]",
        ),
        result_strategy="generic",
        consent_accept_selectors=(
            "input#sp-cc-accept",
            "button:has-text('Accept Cookies')",
        ),
    ),
    "flipkart": BrowserSiteAdapter(
        canonical_name="flipkart",
        aliases=("flipkart.com", "www.flipkart.com"),
        search_input_selectors=(
            "input[title='Search for Products, Brands and More']",
            "input[placeholder*='Search' i]",
            "input[name='q']",
        ),
        result_strategy="generic",
    ),
    "irctc": BrowserSiteAdapter(
        canonical_name="irctc",
        aliases=("irctc.co.in", "www.irctc.co.in", "railway", "railways"),
        search_input_selectors=(
            "input[placeholder*='From']",
            "input[aria-label*='From' i]",
            "input[placeholder*='To']",
            "input[aria-label*='To' i]",
        ),
        result_strategy="generic",
        auth_check_url="https://www.irctc.co.in/nget/train-search",
    ),
    "makemytrip": BrowserSiteAdapter(
        canonical_name="makemytrip",
        aliases=("makemytrip.com", "www.makemytrip.com", "make my trip", "mmt"),
        search_input_selectors=(
            "input[placeholder*='From' i]",
            "input[placeholder*='To' i]",
            "input[aria-label*='From' i]",
            "input[aria-label*='To' i]",
        ),
        result_strategy="generic",
        auth_check_url="https://www.makemytrip.com/railways/",
    ),
    "cleartrip": BrowserSiteAdapter(
        canonical_name="cleartrip",
        aliases=("cleartrip.com", "www.cleartrip.com", "clear trip"),
        search_input_selectors=(
            "input[placeholder*='Where from' i]",
            "input[placeholder*='Where to' i]",
            "input[aria-label*='From' i]",
            "input[aria-label*='To' i]",
        ),
        result_strategy="generic",
    ),
    "redbus": BrowserSiteAdapter(
        canonical_name="redbus",
        aliases=("redbus.in", "www.redbus.in", "red bus"),
        search_input_selectors=(
            "input#src",
            "input#dest",
            "input[placeholder*='Source' i]",
            "input[placeholder*='Destination' i]",
        ),
        result_strategy="generic",
    ),
    "swiggy": BrowserSiteAdapter(
        canonical_name="swiggy",
        aliases=("swiggy.com", "www.swiggy.com"),
        search_input_selectors=(
            "input[placeholder*='Search' i]",
            "input[aria-label*='Search' i]",
        ),
        result_strategy="generic",
        auth_check_url="https://www.swiggy.com/restaurants",
    ),
    "zomato": BrowserSiteAdapter(
        canonical_name="zomato",
        aliases=("zomato.com", "www.zomato.com"),
        search_input_selectors=(
            "input[placeholder*='Search' i]",
            "input[aria-label*='Search' i]",
        ),
        result_strategy="generic",
        auth_check_url="https://www.zomato.com/",
    ),
    "paytm": BrowserSiteAdapter(
        canonical_name="paytm",
        aliases=("paytm.com", "www.paytm.com"),
        search_input_selectors=(
            "input[placeholder*='Search' i]",
            "input[aria-label*='Search' i]",
        ),
        result_strategy="generic",
        auth_check_url="https://paytm.com/",
    ),
    "zepto": BrowserSiteAdapter(
        canonical_name="zepto",
        aliases=("zeptonow.com", "www.zeptonow.com", "zepto"),
        search_input_selectors=(
            "input[placeholder*='Search' i]",
            "input[aria-label*='Search' i]",
        ),
        result_strategy="generic",
    ),
    "blinkit": BrowserSiteAdapter(
        canonical_name="blinkit",
        aliases=("blinkit.com", "www.blinkit.com", "grofers"),
        search_input_selectors=(
            "input[placeholder*='Search' i]",
            "input[aria-label*='Search' i]",
        ),
        result_strategy="generic",
    ),
    "ola": BrowserSiteAdapter(
        canonical_name="ola",
        aliases=("olacabs.com", "www.olacabs.com", "ola cabs"),
        search_input_selectors=(
            "input[placeholder*='Pickup' i]",
            "input[placeholder*='Drop' i]",
            "input[aria-label*='Pickup' i]",
            "input[aria-label*='Drop' i]",
        ),
        result_strategy="generic",
    ),
    "hdfc netbanking": BrowserSiteAdapter(
        canonical_name="hdfc netbanking",
        aliases=("netbanking.hdfcbank.com", "hdfc", "hdfc bank"),
        result_strategy="generic",
        auth_check_url="https://netbanking.hdfcbank.com/netbanking/",
    ),
    "sbi netbanking": BrowserSiteAdapter(
        canonical_name="sbi netbanking",
        aliases=("retail.onlinesbi.sbi", "sbi", "sbi bank", "onlinesbi"),
        result_strategy="generic",
        auth_check_url="https://retail.onlinesbi.sbi/retail/login.htm",
    ),
    "linkedin": BrowserSiteAdapter(
        canonical_name="linkedin",
        aliases=("linkedin.com", "www.linkedin.com"),
        search_input_selectors=(
            "input.search-global-typeahead__input",
            "input[aria-label*='Search' i]",
            "input[placeholder*='Search' i]",
        ),
        result_strategy="generic",
        auth_check_url="https://www.linkedin.com/feed/",
        consent_accept_selectors=(
            "button[action-type='ACCEPT']",
            "button:has-text('Accept')",
        ),
    ),
    "stackoverflow": BrowserSiteAdapter(
        canonical_name="stackoverflow",
        aliases=("stackoverflow.com", "www.stackoverflow.com", "stack overflow", "so"),
        search_input_selectors=(
            "input#search",
            "input[name='q']",
            "input[placeholder*='Search' i]",
        ),
        result_strategy="generic",
        consent_accept_selectors=(
            "button.js-accept-cookies",
            "button[data-consent-trigger='general']",
            "button:has-text('Accept all cookies')",
        ),
    ),
    "wikipedia": BrowserSiteAdapter(
        canonical_name="wikipedia",
        aliases=("wikipedia.org", "en.wikipedia.org", "wiki"),
        search_input_selectors=(
            "input#searchInput",
            "input[name='search']",
            "input[placeholder*='Search' i]",
        ),
        result_strategy="generic",
    ),
    "hackernews": BrowserSiteAdapter(
        canonical_name="hackernews",
        aliases=("news.ycombinator.com", "hacker news", "hn"),
        search_input_selectors=(
            "input[name='q']",
            "input[placeholder*='Search' i]",
        ),
        result_strategy="generic",
    ),
    "npm": BrowserSiteAdapter(
        canonical_name="npm",
        aliases=("npmjs.com", "www.npmjs.com"),
        search_input_selectors=(
            "input#search",
            "input[placeholder*='Search' i]",
        ),
        result_strategy="generic",
    ),
    "pypi": BrowserSiteAdapter(
        canonical_name="pypi",
        aliases=("pypi.org", "www.pypi.org"),
        search_input_selectors=(
            "input#search",
            "input[name='q']",
        ),
        result_strategy="generic",
    ),
    "spotify": BrowserSiteAdapter(
        canonical_name="spotify",
        aliases=("open.spotify.com", "spotify.com"),
        search_input_selectors=(
            "input[data-testid='search-input']",
            "input[placeholder*='What do you want to play' i]",
            "input[placeholder*='Search' i]",
        ),
        result_strategy="generic",
        auth_check_url="https://open.spotify.com/browse",
        consent_accept_selectors=(
            "button[data-testid='accept-button']",
            "button:has-text('Accept Cookies')",
        ),
    ),
    "netflix": BrowserSiteAdapter(
        canonical_name="netflix",
        aliases=("netflix.com", "www.netflix.com"),
        search_input_selectors=(
            "input.searchInput",
            "input[placeholder*='Titles, people, genres' i]",
        ),
        result_strategy="generic",
    ),
    "instagram": BrowserSiteAdapter(
        canonical_name="instagram",
        aliases=("instagram.com", "www.instagram.com", "insta"),
        search_input_selectors=(
            "input[placeholder*='Search' i]",
            "input[aria-label*='Search' i]",
        ),
        result_strategy="generic",
        consent_accept_selectors=(
            "button:has-text('Allow all cookies')",
            "button:has-text('Accept All')",
        ),
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
