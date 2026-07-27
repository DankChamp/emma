"""
AquaClient — Emma's bridge to Aqua.

Lets Emma talk to Aqua's API directly: create documents, notes, flashcards,
search the knowledge base, or just ask Aqua a research question.

This is the counterpart to Luna's EmmaBridge — except Aqua is a subordinate,
so Emma holds the client and calls the shots.
"""
from typing import Optional
from urllib.parse import quote

import httpx


class AquaClient:
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

    async def chat(self, message: str, task_type: str = "conversation",
                   provider: Optional[str] = None, model: Optional[str] = None) -> Optional[str]:
        try:
            payload = {"message": message, "task_type": task_type}
            if provider:
                payload["provider"] = provider
            if model:
                payload["model"] = model
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{self.api_url}/chat", json=payload, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("reply")
        except httpx.HTTPError:
            return None
        return None

    async def create_document(self, title: str, content: str = "",
                              authors: str = "", source: str = "manual",
                              tags: Optional[list[str]] = None) -> Optional[dict]:
        try:
            payload = {"title": title, "content": content, "authors": authors,
                       "source": source}
            if tags:
                payload["tags"] = tags
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{self.api_url}/documents", json=payload, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            return None
        return None

    async def search_documents(self, query: str, limit: int = 10) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.api_url}/documents/search/{quote(query, safe='')}",
                    params={"limit": limit},
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            return []
        return []

    async def list_documents(self, limit: int = 20) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.api_url}/documents",
                    params={"limit": limit},
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            return []
        return []

    async def create_note(self, content: str, title: str = "",
                          document_id: Optional[int] = None) -> Optional[dict]:
        try:
            payload = {"content": content, "title": title}
            if document_id is not None:
                payload["document_id"] = document_id
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(f"{self.api_url}/notes", json=payload, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            return None
        return None

    async def create_flashcard(self, question: str, answer: str,
                               topic: str = "", difficulty: int = 1) -> Optional[dict]:
        try:
            payload = {"question": question, "answer": answer,
                       "topic": topic, "difficulty": difficulty}
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(f"{self.api_url}/flashcards", json=payload, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            return None
        return None

    async def list_flashcards(self, topic: Optional[str] = None, limit: int = 50) -> list[dict]:
        try:
            params: dict = {"limit": limit}
            if topic:
                params["topic"] = topic
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self.api_url}/flashcards", params=params, headers=self._headers())
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            return []
        return []
