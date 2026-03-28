"""Browser workflow recipes and site metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserWorkflowRecipe:
    name: str
    description: str
    examples: tuple[str, ...]
    low_risk: bool = True


SITE_URLS: dict[str, str] = {
    # Search & productivity
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "google calendar": "https://calendar.google.com",
    "google drive": "https://drive.google.com",
    "google docs": "https://docs.google.com",
    "google maps": "https://maps.google.com",
    # Dev & tech
    "github": "https://github.com",
    "leetcode": "https://leetcode.com",
    "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com",
    "npm": "https://www.npmjs.com",
    "pypi": "https://pypi.org",
    "docker hub": "https://hub.docker.com",
    # Social
    "twitter": "https://twitter.com",
    "x": "https://twitter.com",
    "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    # Shopping
    "amazon": "https://www.amazon.com",
    "amazon india": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
    "ebay": "https://www.ebay.com",
    "irctc": "https://www.irctc.co.in",
    "makemytrip": "https://www.makemytrip.com",
    "cleartrip": "https://www.cleartrip.com",
    "redbus": "https://www.redbus.in",
    "swiggy": "https://www.swiggy.com",
    "zomato": "https://www.zomato.com",
    "paytm": "https://paytm.com",
    "zepto": "https://www.zeptonow.com",
    "blinkit": "https://blinkit.com",
    "ola": "https://www.olacabs.com",
    "hdfc netbanking": "https://netbanking.hdfcbank.com",
    "sbi netbanking": "https://retail.onlinesbi.sbi",
    # News & reference
    "wikipedia": "https://en.wikipedia.org",
    "bbc": "https://www.bbc.com",
    "cnn": "https://www.cnn.com",
    "hacker news": "https://news.ycombinator.com",
    "hackernews": "https://news.ycombinator.com",
    # Streaming / media
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "twitch": "https://www.twitch.tv",
}

SITE_LOGIN_URLS: dict[str, str] = {
    "youtube": "https://accounts.google.com/ServiceLogin?service=youtube",
    "gmail": "https://accounts.google.com/ServiceLogin?service=mail",
    "google calendar": "https://accounts.google.com/ServiceLogin",
    "google drive": "https://accounts.google.com/ServiceLogin",
    "github": "https://github.com/login",
    "leetcode": "https://leetcode.com/accounts/login/",
    "twitter": "https://twitter.com/login",
    "x": "https://twitter.com/login",
    "reddit": "https://www.reddit.com/login",
    "linkedin": "https://www.linkedin.com/login",
    "instagram": "https://www.instagram.com/accounts/login/",
    "facebook": "https://www.facebook.com/login",
    "amazon": "https://www.amazon.com/ap/signin",
    "flipkart": "https://www.flipkart.com/account/login",
    "irctc": "https://www.irctc.co.in/nget/train-search",
    "makemytrip": "https://www.makemytrip.com/",
    "cleartrip": "https://www.cleartrip.com/",
    "redbus": "https://www.redbus.in/",
    "swiggy": "https://www.swiggy.com/",
    "zomato": "https://www.zomato.com/",
    "paytm": "https://paytm.com/",
    "ola": "https://www.olacabs.com/",
    "hdfc netbanking": "https://netbanking.hdfcbank.com/netbanking/",
    "sbi netbanking": "https://retail.onlinesbi.sbi/retail/login.htm",
    "netflix": "https://www.netflix.com/login",
    "spotify": "https://accounts.spotify.com/login",
}

SITE_ALIASES: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube", "youtube.com", "yt"),
    "google": ("google", "google.com"),
    "gmail": ("gmail", "mail", "google mail", "mail.google.com"),
    "google calendar": ("google calendar", "gcal", "calendar.google.com", "calendar"),
    "google drive": ("google drive", "gdrive", "drive.google.com", "drive"),
    "github": ("github", "github.com"),
    "leetcode": ("leetcode", "leet code", "leetcode.com"),
    "stackoverflow": ("stackoverflow", "stack overflow", "stackoverflow.com", "so"),
    "twitter": ("twitter", "twitter.com", "x.com", "x", "tweet"),
    "reddit": ("reddit", "reddit.com"),
    "linkedin": ("linkedin", "linkedin.com"),
    "instagram": ("instagram", "instagram.com", "insta"),
    "facebook": ("facebook", "facebook.com", "fb"),
    "amazon": ("amazon", "amazon.com", "amazon.in"),
    "amazon india": ("amazon india", "amazon.in", "www.amazon.in"),
    "flipkart": ("flipkart", "flipkart.com"),
    "ebay": ("ebay", "ebay.com"),
    "irctc": ("irctc", "irctc.co.in", "railway", "railways"),
    "makemytrip": ("makemytrip", "make my trip", "makemytrip.com", "mmt"),
    "cleartrip": ("cleartrip", "clear trip", "cleartrip.com"),
    "redbus": ("redbus", "red bus", "redbus.in"),
    "swiggy": ("swiggy", "swiggy.com"),
    "zomato": ("zomato", "zomato.com"),
    "paytm": ("paytm", "paytm.com"),
    "zepto": ("zepto", "zeptonow", "zeptonow.com"),
    "blinkit": ("blinkit", "blinkit.com", "grofers"),
    "ola": ("ola", "ola cabs", "olacabs.com"),
    "hdfc netbanking": ("hdfc", "hdfc bank", "hdfc netbanking", "netbanking.hdfcbank.com"),
    "sbi netbanking": ("sbi", "sbi bank", "sbi netbanking", "onlinesbi", "retail.onlinesbi.sbi"),
    "wikipedia": ("wikipedia", "wiki", "wikipedia.org"),
    "hackernews": ("hacker news", "hackernews", "hn", "news.ycombinator.com"),
    "npm": ("npm", "npmjs", "npmjs.com"),
    "pypi": ("pypi", "pypi.org"),
    "netflix": ("netflix", "netflix.com"),
    "spotify": ("spotify", "spotify.com", "open.spotify.com"),
    "twitch": ("twitch", "twitch.tv"),
}

LOGIN_FAVORING_SITES = {
    "gmail",
    "leetcode",
    "linkedin",
    "instagram",
    "facebook",
    "netflix",
    "spotify",
    "irctc",
    "makemytrip",
    "cleartrip",
    "redbus",
    "swiggy",
    "zomato",
    "paytm",
    "hdfc netbanking",
    "sbi netbanking",
}

RECIPES: tuple[BrowserWorkflowRecipe, ...] = (
    # ── existing recipes ─────────────────────────────────────
    BrowserWorkflowRecipe(
        name="site_open_exact_url_or_path",
        description="Open an exact URL or domain directly in the browser.",
        examples=(
            "open erp.vcet.edu.in",
            "open https://github.com/Rishiraj-Yadav/SonarBot",
        ),
    ),
    BrowserWorkflowRecipe(
        name="youtube_search_play",
        description="Open YouTube, search for a video title, and open the best matching watch page.",
        examples=(
            "open youtube and play trapped on an island until i build a boat",
            "search youtube for mr beast and play the best match",
        ),
    ),
    BrowserWorkflowRecipe(
        name="youtube_latest_video",
        description="Open YouTube, search a channel or creator, and open the latest-looking matching video result.",
        examples=(
            "play the latest video of MrBeast",
            "open youtube and run the latest mr beast video",
        ),
    ),
    BrowserWorkflowRecipe(
        name="google_search_open",
        description="Open Google, search a query, and open the first or best matching result.",
        examples=(
            "search google for SonarBot GitHub and open the first result",
            "google openai codex and open the best result",
        ),
    ),
    BrowserWorkflowRecipe(
        name="site_open_and_search",
        description="Open a known site and run a site-local search or navigation task.",
        examples=(
            "open leetcode and search arrays problems",
            "open github and search sonarbot",
        ),
    ),
    BrowserWorkflowRecipe(
        name="leetcode_open_problem",
        description="Open a LeetCode problem by number or title-like query.",
        examples=(
            "open leetcode problem 654",
            "open the 654 problem on leetcode",
        ),
    ),
    BrowserWorkflowRecipe(
        name="github_repo_inspect",
        description="Inspect a GitHub repository using API-backed summary data.",
        examples=(
            "tell me about the SonarBot repo",
            "can you tell about this repo",
        ),
    ),
    BrowserWorkflowRecipe(
        name="github_issue_compose",
        description="Open the GitHub issue composer for a repository and pause before submit.",
        examples=(
            "open issue on the SonarBot repo",
            "create an issue in Rishiraj-Yadav/SonarBot",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="site_login_then_continue",
        description="Open a login flow for a known site and optionally resume the last pending browser task.",
        examples=(
            "login to leetcode",
            "log into gmail",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="browser_continue_last_task",
        description="Continue the last blocked or pending browser task after the user clears a blocker.",
        examples=("continue", "do it", "open the first result", "play that one"),
    ),
    # ── new recipes ───────────────────────────────────────────
    BrowserWorkflowRecipe(
        name="generic_page_interact",
        description="Open any URL, enumerate interactive elements, and let the LLM or user decide the next action.",
        examples=(
            "open docs.python.org and click the tutorial link",
            "go to example.com and fill the contact form",
        ),
    ),
    BrowserWorkflowRecipe(
        name="page_read_summarize",
        description="Open a URL, extract the full visible text from the page, and return an LLM-generated summary.",
        examples=(
            "summarize https://en.wikipedia.org/wiki/Python",
            "read and summarize this article: https://example.com/blog",
            "what does this page say: example.com",
        ),
    ),
    BrowserWorkflowRecipe(
        name="multi_tab_research",
        description="Open multiple URLs in parallel browser tabs, extract content from each, and synthesize findings.",
        examples=(
            "open these three links and compare them",
            "research these urls and give me a summary",
        ),
    ),
    BrowserWorkflowRecipe(
        name="twitter_search_scroll",
        description="Open Twitter/X, search for a topic or hashtag, and scroll through the results.",
        examples=(
            "search twitter for python news",
            "open twitter and look up #openai",
            "search x.com for the latest about AI",
        ),
    ),
    BrowserWorkflowRecipe(
        name="reddit_search_open",
        description="Open Reddit, search for a topic, and open the top matching post or subreddit.",
        examples=(
            "search reddit for python tips",
            "open reddit and find the best post about machine learning",
            "what does reddit say about this topic",
        ),
    ),
    BrowserWorkflowRecipe(
        name="amazon_search_buy",
        description="Open Amazon (or Flipkart), search for a product, summarize top results, and pause before any purchase.",
        examples=(
            "search amazon for wireless headphones under 2000",
            "find best laptop on flipkart",
            "look up this product on amazon",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="web_form_fill_submit",
        description="Open a URL, fill a web form from structured field data, and pause before submitting.",
        examples=(
            "fill out the contact form at example.com with my name and email",
            "submit this form: name=John, email=john@example.com",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="calendar_book",
        description="Open Google Calendar in headed mode and create an event, pausing before saving.",
        examples=(
            "book a meeting tomorrow at 3pm called Project Sync",
            "create a calendar event for Friday 5pm dentist",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="email_compose_send",
        description="Open Gmail compose, fill to/subject/body, and pause before sending.",
        examples=(
            "send an email to boss@example.com about the project status",
            "compose an email to john saying I'll be late",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="youtube_media_control",
        description="Control a currently playing YouTube video: pause, resume, mute, seek, or change quality.",
        examples=(
            "pause the video",
            "mute the youtube video",
            "skip ahead 30 seconds",
            "resume playback",
        ),
    ),
    BrowserWorkflowRecipe(
        name="train_search_book",
        description="Open IRCTC and search trains between two stations, pausing before any irreversible booking step.",
        examples=(
            "search trains from borivali to jaipur on irctc",
            "book train from mumbai to delhi on irctc",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="flight_search_book",
        description="Open a travel site and search flights, pausing before passenger or payment confirmation.",
        examples=(
            "search flights from mumbai to delhi on makemytrip",
            "find flights from pune to bangalore on cleartrip",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="food_search_order",
        description="Open a food-delivery site, search for a dish or restaurant, and pause before placing the order.",
        examples=(
            "search swiggy for biryani near me",
            "order pizza on zomato",
        ),
        low_risk=False,
    ),
    BrowserWorkflowRecipe(
        name="bill_payment_review",
        description="Open a banking or wallet site in read-only review mode and stop before any money movement.",
        examples=(
            "open paytm and review my electricity bill payment page",
            "open hdfc netbanking and show the login page",
        ),
        low_risk=False,
    ),
)

RECIPE_BY_NAME = {recipe.name: recipe for recipe in RECIPES}
