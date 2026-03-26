"""
iCloud Hide My Email Alias Generator (реальная версия 2026)
"""

import asyncio
import aiohttp
import ssl
import certifi
import logging
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)


class HideMyEmailGenerator:
    BASE_URL = "https://p68-maildomainws.icloud.com/v1/hme"

    def __init__(self, cookies: str):
        self.cookies = cookies.strip()
        self.label = "Bitrue Registration"
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(
            ssl_context=ssl.create_default_context(cafile=certifi.where())
        )
        self.session = aiohttp.ClientSession(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Origin": "https://www.icloud.com",
                "Referer": "https://www.icloud.com/",
                "Cookie": self.cookies,
            },
            timeout=aiohttp.ClientTimeout(total=20),
            connector=connector,
        )
        logger.info("HideMyEmail session started")
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()
            logger.info("HideMyEmail session closed")

    async def generate(self) -> dict:
        async with self.session.post(f"{self.BASE_URL}/generate", json={"langCode": "en-us"}) as resp:
            data = await resp.json()
            logger.info(f"Generate response: {data}")
            return data

    async def reserve(self, email: str) -> dict:
        payload = {"hme": email, "label": self.label, "note": "Bitrue registration"}
        async with self.session.post(f"{self.BASE_URL}/reserve", json=payload) as resp:
            data = await resp.json()
            logger.info(f"Reserve response for {email}: {data}")
            return data

    async def generate_and_reserve(self) -> dict:
        gen = await self.generate()

        if not gen.get("success"):
            return {"ok": False, "error": gen.get("error", "Generate failed")}

        email = None
        if isinstance(gen.get("result"), dict):
            email = gen["result"].get("hme")

        if not email:
            logger.error(f"Не удалось извлечь email: {gen}")
            return {"ok": False, "error": "No email received"}

        logger.info(f"Generated email: {email}. Waiting 12 seconds before reserve...")
        await asyncio.sleep(12)

        res = await self.reserve(email)

        if not res.get("success"):
            error = res.get("error", {}).get("errorMessage", "Reserve failed")
            logger.error(f"✗ Не удалось зарезервировать: {error}")
            return {"ok": False, "error": error}

        logger.info(f"✅ SUCCESSFULLY CREATED AND RESERVED: {email}")
        return {"ok": True, "email": email}
