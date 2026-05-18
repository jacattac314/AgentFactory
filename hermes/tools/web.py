"""Web tool adapter — search and fetch web pages.

Optional env vars:
  SERPER_API_KEY   — for Google search via serper.dev (free tier: 2500 queries/month)

Falls back to DuckDuckGo HTML scraping if no key is set.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


class WebAdapter:
    """Fetch URLs and search the web."""

    ALLOWED_OPS = {"fetch_url", "search"}

    def __init__(self, serper_api_key: Optional[str] = None):
        self._serper_key = serper_api_key or os.environ.get("SERPER_API_KEY", "")

    # ── Allowed operations ────────────────────────────────────────────────────

    def fetch_url(self, url: str, max_chars: int = 8000) -> Dict[str, Any]:
        """Fetch a URL and return its text content."""
        try:
            import httpx
            from bs4 import BeautifulSoup

            headers = {"User-Agent": "Mozilla/5.0 (Hermes Agent; research purposes)"}
            resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)
            lines = [l for l in text.splitlines() if l.strip()]
            content = "\n".join(lines)[:max_chars]

            return {
                "url":         url,
                "status_code": resp.status_code,
                "content":     content,
                "truncated":   len(content) >= max_chars,
            }
        except Exception as e:
            return {"url": url, "error": str(e)}

    def search(self, query: str, num_results: int = 5) -> List[Dict[str, Any]]:
        """Search the web. Uses Serper if SERPER_API_KEY is set, else DuckDuckGo."""
        if self._serper_key:
            return self._serper_search(query, num_results)
        return self._ddg_search(query, num_results)

    # ── Search backends ───────────────────────────────────────────────────────

    def _serper_search(self, query: str, num: int) -> List[Dict[str, Any]]:
        try:
            import httpx
            resp = httpx.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": self._serper_key, "Content-Type": "application/json"},
                json={"q": query, "num": num},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet")}
                for r in data.get("organic", [])[:num]
            ]
        except Exception as e:
            return [{"error": str(e)}]

    def _ddg_search(self, query: str, num: int) -> List[Dict[str, Any]]:
        """DuckDuckGo HTML search — no API key required."""
        try:
            import httpx
            from bs4 import BeautifulSoup

            params = {"q": query, "kl": "us-en"}
            headers = {"User-Agent": "Mozilla/5.0 (Hermes Agent; research)"}
            resp = httpx.get("https://html.duckduckgo.com/html/", params=params,
                             headers=headers, follow_redirects=True, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result__body")[:num]:
                title_el   = r.select_one(".result__title")
                url_el     = r.select_one(".result__url")
                snippet_el = r.select_one(".result__snippet")
                results.append({
                    "title":   title_el.get_text(strip=True)   if title_el   else "",
                    "url":     url_el.get_text(strip=True)     if url_el     else "",
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                })
            return results or [{"message": "No results found"}]
        except Exception as e:
            return [{"error": str(e)}]

    # ── Metadata ──────────────────────────────────────────────────────────────

    @staticmethod
    def tool_definitions() -> List[Dict[str, Any]]:
        return [
            {
                "name": "web_search",
                "description": "Search the web for information. Returns titles, URLs, and snippets.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query":       {"type": "string",  "description": "Search query"},
                        "num_results": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "web_fetch_url",
                "description": "Fetch a web page and return its text content.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url":       {"type": "string",  "description": "URL to fetch"},
                        "max_chars": {"type": "integer", "description": "Max characters to return (default 8000)", "default": 8000},
                    },
                    "required": ["url"],
                },
            },
        ]

    def call(self, tool_name: str, inputs: Dict[str, Any]) -> Any:
        if tool_name == "web_search":
            return self.search(**inputs)
        if tool_name == "web_fetch_url":
            return self.fetch_url(**inputs)
        raise ValueError(f"Unknown web tool: {tool_name}")
