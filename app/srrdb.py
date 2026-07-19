from __future__ import annotations

import asyncio
from collections.abc import Callable
from urllib.parse import quote

import httpx

BASE_URL = "https://api.srrdb.com/v1"


class ScanCancelled(Exception):
    pass


async def _interruptible_sleep(
    delay_seconds: float,
    stop_requested: Callable[[], bool] | None,
) -> None:
    remaining = max(delay_seconds, 0.0)

    while remaining > 0:
        if stop_requested and stop_requested():
            raise ScanCancelled("Scan stopped by user.")

        step = min(0.25, remaining)
        await asyncio.sleep(step)
        remaining -= step


async def _interruptible_get(
    client: httpx.AsyncClient,
    url: str,
    stop_requested: Callable[[], bool] | None,
) -> httpx.Response:
    request_task = asyncio.create_task(client.get(url))

    try:
        while not request_task.done():
            if stop_requested and stop_requested():
                request_task.cancel()
                try:
                    await request_task
                except asyncio.CancelledError:
                    pass
                raise ScanCancelled("Scan stopped by user.")

            await asyncio.sleep(0.25)

        return await request_task
    finally:
        if not request_task.done():
            request_task.cancel()


async def check_exact_release(
    release_name: str,
    delay_seconds: float,
    stop_requested: Callable[[], bool] | None = None,
) -> tuple[str, str | None, str | None]:
    encoded = quote(release_name, safe="")
    url = f"{BASE_URL}/details/{encoded}"

    if stop_requested and stop_requested():
        raise ScanCancelled("Scan stopped by user.")

    try:
        timeout = httpx.Timeout(
            connect=10.0,
            read=20.0,
            write=10.0,
            pool=10.0,
        )

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "iSiTSCENE/0.5 "
                    "(+https://github.com/insaneavi/isitscene)"
                )
            },
        ) as client:
            response = await _interruptible_get(
                client,
                url,
                stop_requested,
            )

        await _interruptible_sleep(delay_seconds, stop_requested)

        if response.status_code == 404:
            return "unverified", None, "No matching release was found in SRRDB."

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

        return (
            "unverified",
            None,
            "SRRDB response did not contain a recognizable exact release name.",
        )

    except ScanCancelled:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return "unverified", None, "No matching release was found in SRRDB."
        return "unverified", None, f"HTTP {exc.response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        return "unverified", None, str(exc)
