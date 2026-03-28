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
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "leetcode": "https://leetcode.com",
}

SITE_LOGIN_URLS: dict[str, str] = {
    "youtube": "https://accounts.google.com/ServiceLogin?service=youtube",
    "gmail": "https://accounts.google.com/ServiceLogin?service=mail",
    "github": "https://github.com/login",
    "leetcode": "https://leetcode.com/accounts/login/",
}

SITE_ALIASES: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube", "youtube.com", "yt"),
    "google": ("google", "google.com"),
    "gmail": ("gmail", "mail", "google mail", "mail.google.com"),
    "github": ("github", "github.com"),
    "leetcode": ("leetcode", "leet code", "leetcode.com"),
}

LOGIN_FAVORING_SITES = {"gmail", "leetcode"}

RECIPES: tuple[BrowserWorkflowRecipe, ...] = (
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
)

RECIPE_BY_NAME = {recipe.name: recipe for recipe in RECIPES}
