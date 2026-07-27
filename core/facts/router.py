import logging

logger = logging.getLogger("emma.facts")


def classify_topic(fact: str) -> list[str]:
    fact_lower = fact.lower()
    topics = []
    coding_keywords = [
        "code", "programming", "python", "javascript", "rust", "go lang",
        "typescript", "react", "api", "backend", "frontend", "git", "github",
        "debug", "function", "class", "variable", "script", "repository",
        "cli", "terminal", "linux", "docker", "kubernetes", "deploy",
        "algorithm", "data structure", "framework", "library", "npm", "pip",
        "compiler", "editor", "ide", "vscode", "neovim", "vim",
    ]
    research_keywords = [
        "research", "study", "learn", "paper", "article", "book", "document",
        "science", "physics", "math", "biology", "chemistry", "engineering",
        "history", "philosophy", "psychology", "economics", "medicine",
        "jet engine", "aerospace", "machine learning", "deep learning",
        "neural network", "algorithm", "theory", "concept",
    ]
    for kw in coding_keywords:
        if kw in fact_lower:
            topics.append("coding")
            break
    for kw in research_keywords:
        if kw in fact_lower:
            topics.append("research")
            break
    if not topics:
        topics.append("general")
    return topics


class FactRouter:
    def __init__(self, settings):
        self.settings = settings

    def _auth_headers(self, api_key: str) -> dict:
        h = {"Content-Type": "application/json"}
        if api_key:
            h["Authorization"] = f"Bearer {api_key}"
        return h

    async def push_fact(self, fact: str, topics: list[str] | None = None) -> dict:
        import httpx

        if topics is None:
            topics = classify_topic(fact)
        results = {"fact": fact, "topics": topics, "pushed_to": []}

        if "coding" in topics or "general" in topics:
            try:
                headers = self._auth_headers(self.settings.luna_api_key)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{self.settings.luna_api_url}/api/ingest",
                        json={"fact": fact, "tags": topics},
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        results["pushed_to"].append("luna")
            except Exception as e:
                logger.warning("Failed to push fact to Luna: %s", e)

        if "research" in topics or "general" in topics:
            try:
                headers = self._auth_headers(self.settings.aqua_api_key)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{self.settings.aqua_api_url}/api/facts",
                        json={"fact": fact, "tags": topics},
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        results["pushed_to"].append("aqua")
            except Exception as e:
                logger.warning("Failed to push fact to Aqua: %s", e)

        return results
