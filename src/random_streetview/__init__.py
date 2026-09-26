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

import requests

__version__ = "0.2.0"
__all__ = ["Panorama", "random_panorama", "PLATFORMS"]

# Per-request timeout: the default, and the floor any caller is held to.
_DEFAULT_TIMEOUT = 3.0
_MIN_TIMEOUT = 0.5

# Total timeout a single call may budget across its attempts. Measured in
# timeout units rather than wall clock, so time actually spent on the wire
# does not count against it.
_TIMEOUT_BUDGET = 30.0

_USER_AGENT = (
    "random-streetview/0.2.0 "
    "(+https://github.com/AngLaboratory/random-streetview)"
)

PLATFORMS = ("google", "naver", "kakao")

# Google's panorama ids are always this long; anything else is a miss.
_PANOID_LENGTH = 22

# Undocumented internal Maps endpoint -- no API key, no guarantees.
_SEARCH_URL = (
    "https://maps.googleapis.com/maps/api/js/GeoPhotoService.SingleImageSearch"
)

# Picking a point at random mostly lands you in the ocean, so Google candidates
# come from sites that keep their own lists of places known to have coverage.
_RSV_URL = "https://randomstreetview.com/"
_RSV_RE = re.compile(r"randomLocations\.all\s*=\s*(\[\{.*?\}\]);", re.S)

_WANDERY_URL = (
    "https://www.wandery.it/random-street/random-streetview-lateral-thinking.php"
)
_WANDERY_RE = re.compile(r"!2m2!1d([-\d.]+)!2d([-\d.]+)", re.S)

# Naver / Kakao cover South Korea, so their candidate coordinates are drawn
# from inland Korea rather than the worldwide scraped lists.
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


# --------------------------------------------------------------------------- #
# Candidate coordinate sources
# --------------------------------------------------------------------------- #
def _from_randomstreetview(timeout: float) -> _LonLat:
    body = _get(_RSV_URL, timeout)
    if body is None:
        return None, None

    match = _RSV_RE.search(body)
    if not match:
        return None, None
    try:
        locations = json.loads(match.group(1))
        point = random.choice(locations)
        return float(point["lng"]), float(point["lat"])
    except (ValueError, KeyError, TypeError, IndexError):
        return None, None


def _from_wandery(timeout: float) -> _LonLat:
    body = _get(_WANDERY_URL, timeout)
    if body is None:
        return None, None

    match = _WANDERY_RE.search(body)
    if not match:
        return None, None
    try:
        return float(match.group(2)), float(match.group(1))
    except ValueError:
        return None, None


def _random_lonlat_world(timeout: float) -> _LonLat:
    for source in (_from_randomstreetview, _from_wandery):
        lon, lat = source(timeout)
        if lon is not None and lat is not None:
            return lon, lat
    return None, None


def _random_lonlat_korea(timeout: float) -> _LonLat:
    """A random inland-Korea coordinate. Local, so it never touches the wire."""
    lat_min, lat_max, lon_min, lon_max, _weight = random.choices(
        _KOREA_BOXES, weights=[box[4] for box in _KOREA_BOXES], k=1
    )[0]
    lat = round(random.uniform(lat_min, lat_max), 7)
    lon = round(random.uniform(lon_min, lon_max), 7)
    return lon, lat


# --------------------------------------------------------------------------- #
# Per-platform panoid resolution
# --------------------------------------------------------------------------- #
def _google_panoid(lon: float, lat: float, timeout: float) -> Optional[str]:
    body = _get(
        f"{_SEARCH_URL}?pb=!1m5!1sapiv3!5sUS!11m2!1m1!1b0"
        f"!2m4!1m2!3d{lat}!4d{lon}!2d50!3m10"
        "!2m2!1sen!2sGB!9m1!1e2!11m4!1m3!1e2!2b1!3e2!4m10!1e1!1e2!1e3!1e4"
        "!1e8!1e6!5m1!1e2!6m1!1e2&callback=callbackfunc",
        timeout,
    )
    if body is None:
        return None

    # The id is the first quoted string in the JSONP payload.
    start = body.find('"')
    end = body.find('"', start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return None

    panoid = body[start + 1 : end]
    # "generic" is what the endpoint returns when it has nothing to offer.
    if panoid == "generic" or len(panoid) != _PANOID_LENGTH:
        return None
    return panoid


def _naver_panoid(lon: float, lat: float, timeout: float) -> Optional[str]:
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
        panoid = features[0]["properties"]["id"]
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    return str(panoid) or None


def _kakao_panoid(lon: float, lat: float, timeout: float) -> Optional[str]:
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
        panoid = street_view["streetList"][0]["id"]
    except (ValueError, KeyError, TypeError, IndexError):
        return None
    return str(panoid) or None


# platform -> (candidate coordinate source, panoid resolver)
_Resolver = Callable[[float, float, float], Optional[str]]
_CoordSource = Callable[[float], _LonLat]
_PLATFORM_PIPELINE: dict = {
    "google": (_random_lonlat_world, _google_panoid),
    "naver": (_random_lonlat_korea, _naver_panoid),
    "kakao": (_random_lonlat_korea, _kakao_panoid),
}


def random_panorama(
    *,
    platform: str = "google",
    max_retry: int = 10,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Optional[Panorama]:
    """A random Street View panorama.

    ``platform`` selects the provider:

    * ``"google"`` (default) -- a panorama from anywhere on Earth.
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
    if platform not in _PLATFORM_PIPELINE:
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

    coord_source, resolve_panoid = _PLATFORM_PIPELINE[platform]

    budgeted = 0.0
    for _ in range(max_retry):
        if budgeted + timeout > _TIMEOUT_BUDGET:
            break
        budgeted += timeout

        lon, lat = coord_source(timeout)
        if lon is None or lat is None:
            continue
        panoid = resolve_panoid(lon, lat, timeout)
        if panoid is not None:
            return Panorama(panoid, lon, lat, platform)

    return None
