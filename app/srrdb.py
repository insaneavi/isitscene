from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx

from .config import SRRDB_DELAY_SECONDS

BASE_URL = "https://api.srrdb.com/v1"


async def check_exact_release(
    release_name: str,
) -> tuple[str, str | None, str | None]:
    url = f"{BASE_URL}/details/{quote(release_name, safe='')}"

    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "iSiTSCENE/0.2 "
                    "(+https://github.com/insaneavi/isitscene)"
                )
            },
        ) as client:
            response = await client.get(url)

        await asyncio.sleep(SRRDB_DELAY_SECONDS)

        if response.status_code == 404:
            return "not_found", None, None

        response.raise_for_status()
        payload = response.json()
        candidates: list[str] = []

        if isinstance(payload, dict):
            for key in ("release", "releaseName", "name"):
                value = payload.get(key)
                if isinstance(value, str):
                    candidates.append(value)

            results = payload.get("results")
            if isinstance(results, list):
                for result in results:
                    if isinstance(result, dict):
                        value = result.get("release")
                        if isinstance(value, str):
                            candidates.append(value)

        exact = next(
            (
                candidate
                for candidate in candidates
                if candidate.casefold() == release_name.casefold()
            ),
            None,
        )

        if exact:
            return "verified", exact, None

        return "api_error", None, "Unrecognized SRRDB response schema"

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return "not_found", None, None
        return "api_error", None, f"HTTP {exc.response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        return "api_error", None, str(exc)
