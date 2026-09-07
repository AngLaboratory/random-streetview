"""One call, one random Google Street View panorama.

    >>> from random_streetview import random_panorama
    >>> random_panorama()
    Panorama(id='ptFCsB5aFyaWDDlpbCliVw', lon=2.2954822, lat=48.8583758)
"""

from __future__ import annotations

import json
import random
import re
from typing import NamedTuple, Optional, Tuple

import requests

__version__ = "0.1.0"
__all__ = ["Panorama", "random_panorama"]

# Per-request timeout: the default, and the floor any caller is held to.
_DEFAULT_TIMEOUT = 3.0
_MIN_TIMEOUT = 0.5

# Total timeout a single call may budget across its attempts. Measured in
# timeout units rather than wall clock, so time actually spent on the wire
# does not count against it.
_TIMEOUT_BUDGET = 30.0

_USER_AGENT = (
    "random-streetview/0.1.0 "
    "(+https://github.com/AngLaboratory/random-streetview)"
)

# Google's panorama ids are always this long; anything else is a miss.
_PANOID_LENGTH = 22

# Undocumented internal Maps endpoint -- no API key, no guarantees.
_SEARCH_URL = (
    "https://maps.googleapis.com/maps/api/js/GeoPhotoService.SingleImageSearch"
)

# Picking a point at random mostly lands you in the ocean, so candidates come
# from sites that keep their own lists of places known to have coverage.
_RSV_URL = "https://randomstreetview.com/"
_RSV_RE = re.compile(r"randomLocations\.all\s*=\s*(\[\{.*?\}\]);", re.S)

_WANDERY_URL = (
    "https://www.wandery.it/random-street/random-streetview-lateral-thinking.php"
)
_WANDERY_RE = re.compile(r"!2m2!1d([-\d.]+)!2d([-\d.]+)", re.S)

_LonLat = Tuple[Optional[float], Optional[float]]


class Panorama(NamedTuple):
    """A Street View panorama and where it is."""

    id: str
    lon: float
    lat: float

    @property
    def url(self) -> str:
        """A google.com/maps link that opens this panorama."""
        return (
            "https://www.google.com/maps/@"
            f"?api=1&map_action=pano&pano={self.id}"
        )


def _get(url: str, timeout: float) -> Optional[str]:
    try:
        response = requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=timeout
        )
        response.raise_for_status()
        return response.text
    except Exception:
        return None


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


def _random_lonlat(timeout: float) -> _LonLat:
    for source in (_from_randomstreetview, _from_wandery):
        lon, lat = source(timeout)
        if lon is not None and lat is not None:
            return lon, lat
    return None, None


def _panoid_at(lon: float, lat: float, timeout: float) -> Optional[str]:
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


def random_panorama(
    *, max_retry: int = 10, timeout: float = _DEFAULT_TIMEOUT
) -> Optional[Panorama]:
    """A random Street View panorama from somewhere on Earth.

    ``timeout`` is the per-request timeout in seconds, raised to
    :data:`_MIN_TIMEOUT` if you ask for less. Attempts stop early once
    ``timeout`` times the attempts made would exceed the 30 second budget, so
    ``max_retry`` is an upper bound rather than a promise -- the defaults are
    sized to reach all 10.

    Returns ``None`` when the attempts are used up without a hit. Nothing here
    raises on a network or parsing failure.
    """
    if max_retry < 1:
        raise ValueError("max_retry must be at least 1")

    timeout = max(timeout, _MIN_TIMEOUT)
    if timeout > _TIMEOUT_BUDGET:
        raise ValueError(
            "timeout must not exceed the {:.0f}s budget".format(_TIMEOUT_BUDGET)
        )

    budgeted = 0.0
    for _ in range(max_retry):
        if budgeted + timeout > _TIMEOUT_BUDGET:
            break
        budgeted += timeout

        lon, lat = _random_lonlat(timeout)
        if lon is None or lat is None:
            continue
        panoid = _panoid_at(lon, lat, timeout)
        if panoid is not None:
            return Panorama(panoid, lon, lat)

    return None
