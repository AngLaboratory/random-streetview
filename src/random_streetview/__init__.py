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
from typing import Callable, NamedTuple, Optional, Tuple

import requests

__version__ = "0.4.0"
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

# Google retired the old GET form of GeoPhotoService.SingleImageSearch (it now
# answers "...is decommissioned and turned down."), but the same search lives
# on at a MapsJSInternalService RPC that takes a POST body and needs no API
# key. We draw our own random worldwide land coordinate and ask this endpoint
# for the nearest panorama, so the distribution is ours to control rather than
# whatever a third-party site happens to serve.
_SINGLE_IMAGE_SEARCH_URL = (
    "https://maps.googleapis.com/$rpc/"
    "google.internal.maps.mapsjs.v1.MapsJsInternalService/SingleImageSearch"
)
_SINGLE_IMAGE_SEARCH_CONTENT_TYPE = "application/json+protobuf"

# How far from the drawn coordinate the endpoint may snap to a panorama. A wide
# radius turns a near-miss on an empty box into a hit on a nearby road.
_GOOGLE_SEARCH_RADIUS_M = 15000.0

# Weighted worldwide latitude/longitude boxes with dense Street View coverage.
# Weights are rough land-area/coverage proportions so a draw is spread across
# the globe instead of piling up in any one country.
#   (lat_min, lat_max, lon_min, lon_max, weight)
_WORLD_BOXES = (
    (26.0, 52.0, -124.0, -74.0, 7.0),     # contiguous USA
    (35.0, 60.0, -6.0, 53.0, 11.0),       # Europe / Turkey / western Russia
    (-25.0, -3.0, -71.0, -39.0, 7.5),     # central Brazil / Bolivia / Paraguay
    (-36.0, -18.0, 133.0, 152.0, 3.5),    # eastern Australia
    (20.0, 28.0, 68.0, 109.0, 3.5),       # northern India / Bangladesh / southern China
    (-34.0, -17.0, 15.0, 33.0, 3.0),      # southern Africa
    (-40.0, -25.0, -73.0, -51.0, 3.0),    # central Chile / Argentina / Uruguay / S. Brazil
    (-2.0, 20.0, 98.0, 106.0, 2.0),       # Thailand / Indochina / Malay Peninsula
    (51.0, 58.0, 53.0, 95.0, 2.0),        # southern Siberia / northern Kazakhstan
    (4.0, 13.0, -4.0, 12.0, 1.5),         # West Africa (Ghana / Nigeria)
    (60.0, 70.0, 8.0, 36.0, 1.5),         # northern Scandinavia / Finland
    (-35.0, -20.0, 114.0, 122.0, 1.0),    # southwestern Australia
    (8.0, 20.0, 72.0, 81.0, 1.0),         # southern India
    (-4.0, 3.0, 108.0, 123.0, 1.0),       # Borneo / Sulawesi
    (45.0, 51.0, 91.0, 117.0, 1.0),       # Mongolia
    (52.0, 57.0, -133.0, -98.0, 1.0),     # western Canada (prairies / BC)
    (-4.0, 4.0, 29.0, 41.0, 1.0),         # East Africa (Kenya / Uganda)
    (23.0, 27.0, 32.0, 58.0, 1.0),        # Egypt / Saudi Arabia / UAE band
    (16.0, 26.0, -106.0, -96.0, 1.0),     # central Mexico
    (28.0, 36.0, 68.0, 80.0, 1.0),        # Pakistan / northwestern India
    (-53.0, -40.0, -74.0, -65.0, 1.0),    # Patagonia
    (32.0, 38.0, 125.0, 141.0, 1.0),      # Korea / Japan (Seoul–Tokyo band)
)

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


def _post(
    url: str, data: str, timeout: float, *, headers: Optional[dict] = None
) -> Optional[str]:
    hdrs = {"User-Agent": _USER_AGENT}
    if headers:
        hdrs.update(headers)
    try:
        response = requests.post(url, data=data, headers=hdrs, timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception:
        return None


# A found panorama: (panoid, lon, lat), or None on a miss. Each platform's
# attempt function makes a single round trip and returns this.
_Found = Optional[Tuple[str, float, float]]


# --------------------------------------------------------------------------- #
# Google: random worldwide coordinate -> nearest panorama via SingleImageSearch
# --------------------------------------------------------------------------- #
def _random_lonlat_world() -> _LonLat:
    """A random land coordinate. Local, so it never touches the wire."""
    lat_min, lat_max, lon_min, lon_max, _weight = random.choices(
        _WORLD_BOXES, weights=[box[4] for box in _WORLD_BOXES], k=1
    )[0]
    lat = round(random.uniform(lat_min, lat_max), 7)
    lon = round(random.uniform(lon_min, lon_max), 7)
    return lon, lat


def _single_image_search_body(lat: float, lon: float) -> str:
    """The json+protobuf POST body SingleImageSearch expects for a radius query.

    Mirrors the message the Maps JS client sends: an ``apiv3`` header, the
    query point with a search radius, a locale/image-type block (image type 2
    is first-party Google Street View), and a toggle list selecting which
    metadata to return.
    """
    message = [
        ["apiv3", None, None, None, "US"],
        [[None, None, lat, lon], _GOOGLE_SEARCH_RADIUS_M],
        [
            None,
            ["en", "US"],
            None, None, None, None, None, None,
            [2],
            None,
            [[[2, True, 2]]],
        ],
        [[1, 2, 3, 4, 6, 8, 12], None, None, None, [], []],
    ]
    return json.dumps(message)


def _parse_single_image_search(body: str) -> _Found:
    """Pull (panoid, lon, lat) out of a SingleImageSearch response, or None.

    A successful radius search answers ``[0, <pano message>, ...]``; an empty
    one answers ``[[5, "generic", "Search returned no images."]]``. The panoid
    lives at ``msg[1][1]`` and its coordinates at ``msg[5][0][1][0][2:4]``.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return None
    if data[0][0] != 0:  # 0 == OK; 5 == no images; anything else == not a hit
        return None
    try:
        msg = data[1]
        panoid = str(msg[1][1])
        lat = float(msg[5][0][1][0][2])
        lon = float(msg[5][0][1][0][3])
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    if len(panoid) != _PANOID_LENGTH:
        return None
    return panoid, lon, lat


def _google_attempt(timeout: float) -> _Found:
    """Draw a random coordinate and return the nearest Google panorama to it."""
    lon, lat = _random_lonlat_world()
    body = _post(
        _SINGLE_IMAGE_SEARCH_URL,
        _single_image_search_body(lat, lon),
        timeout,
        headers={"Content-Type": _SINGLE_IMAGE_SEARCH_CONTENT_TYPE},
    )
    if body is None:
        return None
    return _parse_single_image_search(body)


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

    * ``"google"`` (default) -- a panorama from anywhere on Earth: a random
      land coordinate snapped to its nearest Street View panorama.
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
