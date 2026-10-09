"""One call, one random Street View panorama.

Google (worldwide) is the default. Naver and Kakao are also supported and
return road-view panorama ids from South Korea.

    >>> from random_streetview import random_panorama
    >>> random_panorama()                     # Google, worldwide
    Panorama(id='ptFCsB5aFyaWDDlpbCliVw', lon=2.2954822, lat=48.8583758, platform='google')
    >>> random_panorama(platform="naver")     # Naver, South Korea
    Panorama(id='...', lon=..., lat=..., platform='naver')
    >>> random_panorama(platform="kakao")     # Kakao, South Korea
    Panorama(id='...', lon=..., lat=..., platform='kakao')
"""

from __future__ import annotations

import json
import random
import re
from typing import Callable, NamedTuple, Optional, Tuple
from urllib.parse import quote

import requests

__version__ = "0.3.0"
__all__ = ["Panorama", "random_panorama", "PLATFORMS"]

# Per-request timeout: the default, and the floor any caller is held to.
_DEFAULT_TIMEOUT = 3.0
_MIN_TIMEOUT = 0.5

# Total timeout a single call may budget across its attempts. Measured in
# timeout units rather than wall clock, so time actually spent on the wire
# does not count against it.
_TIMEOUT_BUDGET = 30.0

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

PLATFORMS = ("google", "naver", "kakao")

# Google's panorama ids are always this long; anything else is a miss.
_PANOID_LENGTH = 22

# Google retired GeoPhotoService.SingleImageSearch (it now answers "...is
# decommissioned and turned down."), so a coordinate can no longer be turned
# into a panorama id that way. Instead we scrape a site that embeds a real
# Street View panorama id directly, and confirm the id still resolves through
# the live photometa metadata endpoint.
_WANDERY_URL = (
    "https://www.wandery.it/random-street/random-streetview-lateral-thinking.php"
)
# The embedded Maps iframe carries the panoid then its coordinates:
#   !1s<panoid>!2m2!1d<lat>!2d<lon>
_WANDERY_RE = re.compile(
    r"!1s([A-Za-z0-9_\-]{22})!2m2!1d([-\d.]+)!2d([-\d.]+)", re.S
)

# Live Google endpoint that returns metadata for a known panoid (no API key).
# Used to confirm a scraped panorama still exists before handing it back. This
# is the full pb Google's own viewer sends; a truncated one is rejected (400).
_PHOTOMETA_URL = (
    "https://www.google.com/maps/photometa/v1?authuser=0&hl=en&gl=us&pb="
    "!1m4!1smaps_sv.tactile!11m2!2m1!1b1!2m2!1sen!2sus"
    "!3m3!1m2!1e2!2s{panoid}"
    "!4m57!1e1!1e2!1e4!1e1!1e1!1e1!1e1!1e1"
    "!2m1!1e1!4m1!1i48!5m1!1e1!5m1!1e2!6m1!1e1!6m1!1e2"
    "!9m36!1m3!1e6!2b0!3e0!1m3!1e6!2b0!3e1!1m3!1e6!2b0!3e2"
    "!1m3!1e6!2b0!3e3!1m3!1e6!2b0!3e4!1m3!1e6!2b0!3e5"
    "!1m3!1e6!2b0!3e6!1m3!1e6!2b0!3e7!1m3!1e6!2b0!3e8"
)
# photometa rejects requests without a google.com referer.
_PHOTOMETA_REFERER = "https://www.google.com/"

# Naver / Kakao cover South Korea, so their candidate coordinates are drawn
# from inland Korea.
_NAVER_NEARBY_URL = "https://map.naver.com/p/api/panorama/nearby/{lon}/{lat}"
_KAKAO_NODES_URL = (
    "https://rv.map.kakao.com/roadview-search/v2/nodes"
    "?PX={lon}&PY={lat}&RAD=900&INPUT=wgs"
)
_KR_REFERER = "https://map.naver.com/p?c=15.00,0,0,0,adh&isMini=true"

# Weighted inland-Korea latitude/longitude boxes. Weights are roughly
# proportional to land area so denser regions are picked more often.
_KOREA_BOXES = (
    (37.0, 37.9, 126.7, 128.9, 1.5718908797500284),
    (36.0, 37.0, 126.2, 129.4, 2.572341953975098),
    (35.0, 36.0, 126.3, 129.5, 2.6051696587402238),
    (34.5, 35.0, 126.1, 127.7, 0.6573175503537345),
    (33.25, 33.53, 126.15, 126.93, 0.18235175377938712),
)

_LonLat = Tuple[Optional[float], Optional[float]]


class Panorama(NamedTuple):
    """A Street View panorama, where it is, and which platform it came from."""

    id: str
    lon: float
    lat: float
    platform: str = "google"

    @property
    def url(self) -> str:
        """A map link that opens this panorama on its platform."""
        if self.platform == "naver":
            return f"https://map.naver.com/p/panorama/{self.id}"
        if self.platform == "kakao":
            return (
                "https://map.kakao.com/link/roadview/"
                f"{self.lat},{self.lon}"
            )
        return (
            "https://www.google.com/maps/@"
            f"?api=1&map_action=pano&pano={self.id}"
        )


def _get(url: str, timeout: float, *, headers: Optional[dict] = None) -> Optional[str]:
    hdrs = {"User-Agent": _USER_AGENT}
    if headers:
        hdrs.update(headers)
    try:
        response = requests.get(url, headers=hdrs, timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception:
        return None


# A found panorama: (panoid, lon, lat), or None on a miss. Each platform's
# attempt function makes at most one round trip (or two for Google: scrape then
# verify) and returns this.
_Found = Optional[Tuple[str, float, float]]


# --------------------------------------------------------------------------- #
# Google: scrape a real panoid, then confirm it still resolves
# --------------------------------------------------------------------------- #
def _panoid_exists(panoid: str, timeout: float) -> bool:
    """True if ``panoid`` still resolves through the live photometa endpoint."""
    body = _get(
        _PHOTOMETA_URL.format(panoid=quote(panoid)),
        timeout,
        headers={"Referer": _PHOTOMETA_REFERER},
    )
    if not body:
        return False
    # photometa replies with a ")]}'\n" guard then JSON; a valid pano echoes
    # its own id back in the payload. A missing/retired pano does not.
    return panoid in body


def _google_attempt(timeout: float) -> _Found:
    """Scrape wandery.it for an embedded panorama, verify it, and return it."""
    body = _get(_WANDERY_URL, timeout)
    if body is None:
        return None
    match = _WANDERY_RE.search(body)
    if not match:
        return None
    panoid = match.group(1)
    try:
        lat, lon = float(match.group(2)), float(match.group(3))
    except ValueError:
        return None
    if len(panoid) != _PANOID_LENGTH:
        return None
    if not _panoid_exists(panoid, timeout):
        return None
    return panoid, lon, lat


# --------------------------------------------------------------------------- #
# Naver / Kakao: random Korea coordinate -> that service's nearby endpoint
# --------------------------------------------------------------------------- #
def _random_lonlat_korea() -> _LonLat:
    """A random inland-Korea coordinate. Local, so it never touches the wire."""
    lat_min, lat_max, lon_min, lon_max, _weight = random.choices(
        _KOREA_BOXES, weights=[box[4] for box in _KOREA_BOXES], k=1
    )[0]
    lat = round(random.uniform(lat_min, lat_max), 7)
    lon = round(random.uniform(lon_min, lon_max), 7)
    return lon, lat


def _naver_attempt(timeout: float) -> _Found:
    lon, lat = _random_lonlat_korea()
    body = _get(
        _NAVER_NEARBY_URL.format(lon=lon, lat=lat),
        timeout,
        headers={"Referer": _KR_REFERER},
    )
    if body is None:
        return None
    try:
        features = json.loads(body).get("features")
        if not features:
            return None
        panoid = str(features[0]["properties"]["id"])
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    return (panoid, lon, lat) if panoid else None


def _kakao_attempt(timeout: float) -> _Found:
    lon, lat = _random_lonlat_korea()
    body = _get(
        _KAKAO_NODES_URL.format(lon=lon, lat=lat),
        timeout,
        headers={"Referer": _KR_REFERER},
    )
    if body is None:
        return None
    try:
        street_view = json.loads(body).get("street_view")
        if not street_view or street_view.get("cnt", 0) == 0:
            return None
        panoid = str(street_view["streetList"][0]["id"])
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    return (panoid, lon, lat) if panoid else None


# platform -> single-attempt function returning (panoid, lon, lat) or None.
_Attempt = Callable[[float], _Found]
_PLATFORM_ATTEMPT: dict = {
    "google": _google_attempt,
    "naver": _naver_attempt,
    "kakao": _kakao_attempt,
}


def random_panorama(
    *,
    platform: str = "google",
    max_retry: int = 10,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Optional[Panorama]:
    """A random Street View panorama.

    ``platform`` selects the provider:

    * ``"google"`` (default) -- a panorama from anywhere on Earth, scraped
      from wandery.it and confirmed against Google's live metadata endpoint.
    * ``"naver"`` -- a road-view panorama from South Korea.
    * ``"kakao"`` -- a road-view panorama from South Korea.

    ``timeout`` is the per-request timeout in seconds, raised to
    :data:`_MIN_TIMEOUT` if you ask for less. Attempts stop early once
    ``timeout`` times the attempts made would exceed the 30 second budget, so
    ``max_retry`` is an upper bound rather than a promise -- the defaults are
    sized to reach all 10.

    Returns ``None`` when the attempts are used up without a hit. Nothing here
    raises on a network or parsing failure.
    """
    if platform not in _PLATFORM_ATTEMPT:
        raise ValueError(
            "platform must be one of {}".format(", ".join(PLATFORMS))
        )
    if max_retry < 1:
        raise ValueError("max_retry must be at least 1")

    timeout = max(timeout, _MIN_TIMEOUT)
    if timeout > _TIMEOUT_BUDGET:
        raise ValueError(
            "timeout must not exceed the {:.0f}s budget".format(_TIMEOUT_BUDGET)
        )

    attempt = _PLATFORM_ATTEMPT[platform]

    budgeted = 0.0
    for _ in range(max_retry):
        if budgeted + timeout > _TIMEOUT_BUDGET:
            break
        budgeted += timeout

        found = attempt(timeout)
        if found is not None:
            panoid, lon, lat = found
            return Panorama(panoid, lon, lat, platform)

    return None
