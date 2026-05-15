import httpx
import asyncio
import logging
from app.config import get_settings

logger = logging.getLogger(__name__)

_BATCH = 20


async def embed_texts(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    url = (
        f"{s.cloudflare_base_url}/accounts/{s.cloudflare_account_id}"
        f"/ai/run/{s.cloudflare_embedding_model}"
    )
    headers = {
        "Authorization": f"Bearer {s.cloudflare_api_token}",
        "Content-Type": "application/json",
    }
    all_vectors: list[list[float]] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), _BATCH):
            batch = texts[i: i + _BATCH]
            for attempt in range(3):
                try:
                    resp = await client.post(url, headers=headers, json={"text": batch})
                    resp.raise_for_status()
                    all_vectors.extend(resp.json()["result"]["data"])
                    break
                except httpx.HTTPStatusError as e:
                    logger.warning("Cloudflare intento %d: %s", attempt + 1, e)
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)

    return all_vectors


async def embed_single(text: str) -> list[float]:
    return (await embed_texts([text]))[0]
