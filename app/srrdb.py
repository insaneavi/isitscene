from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx

BASE_URL = "https://api.srrdb.com/v1"
DETAILS_URL = "https://www.srrdb.com/release/details"

_YEAR_RE = re.compile(r"^(?:19|20|21)\d{2}$", re.IGNORECASE)
_GROUP_RE = re.compile(r"-([A-Za-z0-9][A-Za-z0-9._-]*)$")
_SPLIT_RE = re.compile(r"[.\s_]+")
_RESOLUTION_RE = re.compile(r"^(?:480[pi]|576[pi]|720p|1080[pi]|2160p|4k)$", re.I)

_SOURCE_TOKENS = {
    "bluray",
    "blu-ray",
    "uhd",
    "hdtv",
    "web",
    "web-dl",
    "webrip",
    "dvdrip",
    "bdrip",
    "brrip",
    "remux",
}

_CODEC_TOKENS = {
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "xvid",
    "av1",
}

_FLAG_TOKENS = {
    "repack",
    "proper",
    "rerip",
    "internal",
    "limited",
    "extended",
    "unrated",
    "remastered",
    "complete",
}

_TECH_START = (
    _SOURCE_TOKENS
    | _CODEC_TOKENS
    | _FLAG_TOKENS
    | {
        "hdr",
        "hdr10",
        "hdr10plus",
        "dv",
        "dovi",
        "atmos",
        "aac",
        "ac3",
        "ddp",
        "dts",
        "truehd",
        "subs",
        "dubbed",
        "multi",
    }
)


class ScanCancelled(Exception):
    pass


@dataclass(frozen=True)
class ParsedRelease:
    release_name: str
    title_tokens: tuple[str, ...]
    year: str | None
    group: str | None
    resolution: str | None
    source: str | None
    codec: str | None
    flags: frozenset[str]


@dataclass(frozen=True)
class CandidateResult:
    release_name: str
    url: str
    score: int
    reason: str


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in _SPLIT_RE.split(value.strip(". _-"))
        if token
    ]


def parse_release_name(release_name: str) -> ParsedRelease:
    group_match = _GROUP_RE.search(release_name)
    group = group_match.group(1) if group_match else None
    body = release_name[:group_match.start()] if group_match else release_name
    raw_tokens = _tokens(body)

    year = next(
        (token for token in raw_tokens if _YEAR_RE.fullmatch(token)),
        None,
    )
    resolution = next(
        (token for token in raw_tokens if _RESOLUTION_RE.fullmatch(token)),
        None,
    )

    lowered = [token.casefold() for token in raw_tokens]
    source = next(
        (token for token in lowered if token in _SOURCE_TOKENS),
        None,
    )
    codec = next(
        (token for token in lowered if token in _CODEC_TOKENS),
        None,
    )
    flags = frozenset(
        token for token in lowered if token in _FLAG_TOKENS
    )

    title_end = len(raw_tokens)
    for index, token in enumerate(raw_tokens):
        folded = token.casefold()
        if (
            _YEAR_RE.fullmatch(token)
            or _RESOLUTION_RE.fullmatch(token)
            or folded in _TECH_START
        ):
            title_end = index
            break

    title_tokens = tuple(
        token.casefold()
        for token in raw_tokens[:title_end]
        if token
    )

    return ParsedRelease(
        release_name=release_name,
        title_tokens=title_tokens,
        year=year,
        group=group,
        resolution=resolution.casefold() if resolution else None,
        source=source,
        codec=codec,
        flags=flags,
    )


def _title_similarity(left: ParsedRelease, right: ParsedRelease) -> float:
    left_title = " ".join(left.title_tokens)
    right_title = " ".join(right.title_tokens)
    if not left_title or not right_title:
        return 0.0

    sequence_score = SequenceMatcher(
        None,
        left_title,
        right_title,
    ).ratio()

    left_set = set(left.title_tokens)
    right_set = set(right.title_tokens)
    union = left_set | right_set
    overlap_score = (
        len(left_set & right_set) / len(union)
        if union
        else 0.0
    )

    return max(sequence_score, overlap_score)


def score_candidate(
    source: ParsedRelease,
    candidate: ParsedRelease,
) -> int:
    title_similarity = _title_similarity(source, candidate)
    if title_similarity < 0.70:
        return 0

    score = round(title_similarity * 40)

    if (
        source.group
        and candidate.group
        and source.group.casefold() == candidate.group.casefold()
    ):
        score += 25

    if source.resolution and source.resolution == candidate.resolution:
        score += 12

    if source.source and source.source == candidate.source:
        score += 8

    if source.codec and source.codec == candidate.codec:
        score += 8

    if source.flags:
        shared_flags = source.flags & candidate.flags
        score += round(7 * len(shared_flags) / len(source.flags))

    # A conflicting known year is a strong negative signal.
    if source.year and candidate.year and source.year != candidate.year:
        score -= 30

    return max(0, min(100, score))


def describe_candidate_difference(
    source: ParsedRelease,
    candidate: ParsedRelease,
) -> str:
    differences: list[str] = []

    if not source.year and candidate.year:
        differences.append(f"Folder may be missing the year {candidate.year}.")
    elif (
        source.year
        and candidate.year
        and source.year != candidate.year
    ):
        differences.append(
            f"Year differs: folder has {source.year}; "
            f"candidate has {candidate.year}."
        )

    if (
        source.group
        and candidate.group
        and source.group.casefold() != candidate.group.casefold()
    ):
        differences.append(
            f"Group differs: folder has {source.group}; "
            f"candidate has {candidate.group}."
        )

    if (
        source.resolution
        and candidate.resolution
        and source.resolution != candidate.resolution
    ):
        differences.append(
            f"Resolution differs: folder has {source.resolution}; "
            f"candidate has {candidate.resolution}."
        )

    if (
        source.source
        and candidate.source
        and source.source != candidate.source
    ):
        differences.append(
            f"Source differs: folder has {source.source}; "
            f"candidate has {candidate.source}."
        )

    if (
        source.codec
        and candidate.codec
        and source.codec != candidate.codec
    ):
        differences.append(
            f"Codec differs: folder has {source.codec}; "
            f"candidate has {candidate.codec}."
        )

    missing_flags = candidate.flags - source.flags
    if missing_flags:
        differences.append(
            "Candidate contains additional tag(s): "
            + ", ".join(sorted(flag.upper() for flag in missing_flags))
            + "."
        )

    if not differences:
        differences.append(
            "Release names are very similar, but are not an exact match."
        )

    return " ".join(differences)


def _extract_release_names(payload: object) -> list[str]:
    releases: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key in (
                "release",
                "releaseName",
                "releasename",
                "name",
            ):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    releases.append(candidate.strip())

            for key in ("results", "result", "data", "items"):
                nested = value.get(key)
                if isinstance(nested, (list, dict)):
                    collect(nested)

        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)

    seen: set[str] = set()
    unique: list[str] = []
    for release in releases:
        folded = release.casefold()
        if folded not in seen:
            seen.add(folded)
            unique.append(release)

    return unique


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


def _search_url(parsed: ParsedRelease) -> str | None:
    # The live SRRDB API currently rejects the documented group: keyword.
    # Search by title words only and use release metadata locally to score
    # the returned candidates.
    meaningful_words = [
        word
        for word in parsed.title_tokens
        if len(word) > 1
    ][:10]

    if not meaningful_words:
        return None

    segments = [quote(word, safe="") for word in meaningful_words]
    return f"{BASE_URL}/search/" + "/".join(segments)


async def find_candidate_release(
    client: httpx.AsyncClient,
    release_name: str,
    delay_seconds: float,
    stop_requested: Callable[[], bool] | None = None,
) -> CandidateResult | None:
    parsed_source = parse_release_name(release_name)
    url = _search_url(parsed_source)

    if not url:
        return None

    try:
        response = await _interruptible_get(
            client,
            url,
            stop_requested,
        )
        await _interruptible_sleep(delay_seconds, stop_requested)

        if response.status_code == 404:
            return None

        response.raise_for_status()
        payload = response.json()
    except ScanCancelled:
        raise
    except (httpx.HTTPError, ValueError):
        # Candidate discovery is advisory. Failure must never fail or
        # incorrectly verify the main scan.
        return None

    scored: list[tuple[int, str, ParsedRelease]] = []
    for candidate_name in _extract_release_names(payload):
        parsed_candidate = parse_release_name(candidate_name)
        score = score_candidate(parsed_source, parsed_candidate)
        if score >= 75:
            scored.append((score, candidate_name, parsed_candidate))

    if not scored:
        return None

    scored.sort(
        key=lambda item: (
            item[0],
            item[1].casefold(),
        ),
        reverse=True,
    )
    score, candidate_name, parsed_candidate = scored[0]

    return CandidateResult(
        release_name=candidate_name,
        url=(
            f"{DETAILS_URL}/"
            f"{quote(candidate_name, safe='.-_')}"
        ),
        score=score,
        reason=describe_candidate_difference(
            parsed_source,
            parsed_candidate,
        ),
    )


async def check_release(
    release_name: str,
    delay_seconds: float,
    stop_requested: Callable[[], bool] | None = None,
) -> tuple[
    str,
    str | None,
    str | None,
    CandidateResult | None,
]:
    encoded = quote(release_name, safe="")
    exact_url = f"{BASE_URL}/details/{encoded}"

    if stop_requested and stop_requested():
        raise ScanCancelled("Scan stopped by user.")

    timeout = httpx.Timeout(
        connect=10.0,
        read=20.0,
        write=10.0,
        pool=10.0,
    )

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "iSiTSCENE/0.9.1 "
                    "(+https://github.com/insaneavi/isitscene)"
                )
            },
        ) as client:
            response = await _interruptible_get(
                client,
                exact_url,
                stop_requested,
            )
            await _interruptible_sleep(
                delay_seconds,
                stop_requested,
            )

            exact_error: str | None = None

            if response.status_code == 404:
                exact_error = "No exact release-name match was found in SRRDB."
            else:
                response.raise_for_status()
                payload = response.json()
                candidates = _extract_release_names(payload)

                exact = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.casefold() == release_name.casefold()
                    ),
                    None,
                )

                if exact:
                    return "verified", exact, None, None

                exact_error = (
                    "SRRDB response did not contain a recognizable "
                    "exact release-name match."
                )

            candidate = await find_candidate_release(
                client,
                release_name,
                delay_seconds,
                stop_requested,
            )

            return "unverified", None, exact_error, candidate

    except ScanCancelled:
        raise
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            error = "No exact release-name match was found in SRRDB."
        else:
            error = f"SRRDB returned HTTP {exc.response.status_code}."

        return "unverified", None, error, None
    except (httpx.HTTPError, ValueError) as exc:
        return "unverified", None, str(exc), None


# Backward-compatible name for callers outside this repository.
async def check_exact_release(
    release_name: str,
    delay_seconds: float,
    stop_requested: Callable[[], bool] | None = None,
) -> tuple[str, str | None, str | None]:
    status, matched, error, _candidate = await check_release(
        release_name,
        delay_seconds,
        stop_requested,
    )
    return status, matched, error


_IMDB_RE = re.compile(r"(?:tt)?(\d{7,9})", re.IGNORECASE)


def _extract_imdb_id(payload: object) -> str | None:
    """Find an IMDb identifier in SRRDB IMDb endpoint responses."""
    preferred_keys = {
        "imdb",
        "imdbid",
        "imdb_id",
        "imdbnumber",
        "imdb_number",
        "id",
    }

    def normalize(value: object) -> str | None:
        if value is None:
            return None
        match = _IMDB_RE.search(str(value))
        if not match:
            return None
        return f"tt{match.group(1)}"

    def walk(value: object) -> str | None:
        if isinstance(value, dict):
            # Prefer known IMDb-related fields first.
            for key, nested in value.items():
                if str(key).casefold() in preferred_keys:
                    found = normalize(nested)
                    if found:
                        return found

            # Then inspect nested payload wrappers used by SRRDB.
            for nested in value.values():
                found = walk(nested)
                if found:
                    return found

        elif isinstance(value, list):
            for nested in value:
                found = walk(nested)
                if found:
                    return found

        elif isinstance(value, (str, int)):
            return normalize(value)

        return None

    return walk(payload)


def is_strict_uhd_upgrade(release_name: str) -> bool:
    """Match the user's exact Scene UHD rule: 2160p + UHD + x265."""
    folded = release_name.casefold()

    def has_tag(tag: str) -> bool:
        return re.search(
            rf"(?:^|[._\s-]){re.escape(tag)}(?:$|[._\s-])",
            folded,
        ) is not None

    return all(has_tag(tag) for tag in ("2160p", "uhd", "x265"))


async def find_uhd_upgrades(
    release_name: str,
    delay_seconds: float,
    stop_requested: Callable[[], bool] | None = None,
) -> tuple[str | None, list[CandidateResult], str | None]:
    """Resolve IMDb from the verified release, then search strict UHD candidates."""
    timeout = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
    encoded = quote(release_name, safe="")

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "iSiTSCENE/0.9.1 "
                    "(+https://github.com/insaneavi/isitscene)"
                )
            },
        ) as client:
            imdb_response = await _interruptible_get(
                client,
                f"{BASE_URL}/imdb/{encoded}",
                stop_requested,
            )
            await _interruptible_sleep(delay_seconds, stop_requested)

            if imdb_response.status_code == 404:
                return None, [], None

            imdb_response.raise_for_status()
            imdb_payload = imdb_response.json()
            imdb_id = _extract_imdb_id(imdb_payload)
            if not imdb_id:
                return (
                    None,
                    [],
                    "SRRDB IMDb response did not contain a recognizable IMDb ID.",
                )

            imdb_digits = imdb_id.removeprefix("tt")
            search_response = await _interruptible_get(
                client,
                f"{BASE_URL}/search/imdb:{quote(imdb_digits, safe='')}",
                stop_requested,
            )
            await _interruptible_sleep(delay_seconds, stop_requested)

            if search_response.status_code == 404:
                return imdb_id, [], None

            search_response.raise_for_status()
            names = _extract_release_names(search_response.json())
            candidates = [
                CandidateResult(
                    release_name=name,
                    url=f"{DETAILS_URL}/{quote(name, safe='.-_')}",
                    score=100,
                    reason="Contains all required Scene UHD tags: 2160p, UHD, and x265.",
                )
                for name in names
                if is_strict_uhd_upgrade(name)
            ]
            candidates.sort(key=lambda item: item.release_name.casefold())
            return imdb_id, candidates, None
    except ScanCancelled:
        raise
    except httpx.HTTPStatusError as exc:
        return None, [], f"SRRDB returned HTTP {exc.response.status_code}."
    except (httpx.HTTPError, ValueError) as exc:
        return None, [], str(exc)
