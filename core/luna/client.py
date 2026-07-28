import httpx


class LunaClient:
    def __init__(self, api_url: str, api_key: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def is_connected(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.api_url}/health", headers=self._headers())
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def chat(self, message: str, system: str = "") -> str | None:
        try:
            payload = {"message": message}
            if system:
                payload["system"] = system
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self.api_url}/api/chat",
                    json=payload,
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("response")
        except httpx.HTTPError:
            return None
        return None

    async def status(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.api_url}/status", headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            return None
        return None

    async def get_history(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.api_url}/history", headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            return None
        return None

    async def ingest_fact(self, fact: str, tags: list[str] | None = None) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                body = {"fact": fact, "tags": tags or []}
                resp = await client.post(f"{self.api_url}/api/ingest", json=body, headers=self._headers())
                return resp.status_code == 200
        except httpx.HTTPError:
            return False
