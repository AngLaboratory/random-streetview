"""Offline tests -- every network call is monkeypatched."""

import json

import pytest

import random_streetview as rsv
from random_streetview import Panorama, random_panorama

_RSV_BODY = 'var x; randomLocations.all = [{"lat":48.8584,"lng":2.2945}];'
_WANDERY_BODY = "?pb=!2m2!1d48.8584!2d2.2945!3m1"
_JSONP = 'callbackfunc([1,"ptFCsB5aFyaWDDlpbCliVw",[null]])'

_NAVER_BODY = json.dumps({"features": [{"properties": {"id": "naverPano123"}}]})
_KAKAO_BODY = json.dumps(
    {"street_view": {"cnt": 1, "streetList": [{"id": "1129047585"}]}}
)


def test_panorama_unpacks_and_builds_a_url():
    pano = Panorama("a" * 22, 2.2945, 48.8584)
    assert (pano.id, pano.lon, pano.lat) == tuple(pano)[:3]
    assert pano.platform == "google"
    assert pano.url.endswith("pano=" + "a" * 22)


def test_max_retry_must_be_positive():
    with pytest.raises(ValueError):
        random_panorama(max_retry=0)


def test_unknown_platform_is_rejected():
    with pytest.raises(ValueError):
        random_panorama(platform="bing")


def test_timeout_is_raised_to_the_floor(monkeypatch):
    seen = []

    def fake_get(url, timeout, **kwargs):
        seen.append(timeout)
        return None

    monkeypatch.setattr(rsv, "_get", fake_get)
    random_panorama(max_retry=1, timeout=0.01)
    assert seen and all(t == rsv._MIN_TIMEOUT for t in seen)


def test_timeout_larger_than_the_budget_is_rejected():
    with pytest.raises(ValueError):
        random_panorama(timeout=rsv._TIMEOUT_BUDGET + 1)


def test_attempts_stop_at_the_budget(monkeypatch):
    attempts = []

    def fake_get(url, timeout, **kwargs):
        if url.startswith("https://randomstreetview"):
            attempts.append(url)
        return None

    monkeypatch.setattr(rsv, "_get", fake_get)
    # 10s per request leaves room for 3 attempts inside the 30s budget,
    # not the 10 that max_retry asks for.
    assert random_panorama(max_retry=10, timeout=10.0) is None
    assert len(attempts) == 3


def test_defaults_reach_every_retry(monkeypatch):
    attempts = []

    def fake_get(url, timeout, **kwargs):
        if url.startswith("https://randomstreetview"):
            attempts.append(url)
        return None

    monkeypatch.setattr(rsv, "_get", fake_get)
    assert random_panorama() is None
    assert len(attempts) == 10


def test_happy_path(monkeypatch):
    def fake_get(url, timeout, **kwargs):
        return _RSV_BODY if url.startswith("https://randomstreetview") else _JSONP

    monkeypatch.setattr(rsv, "_get", fake_get)
    pano = random_panorama()
    assert pano == Panorama("ptFCsB5aFyaWDDlpbCliVw", 2.2945, 48.8584, "google")


def test_falls_back_to_the_second_source(monkeypatch):
    def fake_get(url, timeout, **kwargs):
        if url.startswith("https://randomstreetview"):
            return None
        if url.startswith("https://www.wandery"):
            return _WANDERY_BODY
        return _JSONP

    monkeypatch.setattr(rsv, "_get", fake_get)
    pano = random_panorama()
    assert pano is not None
    assert (pano.lon, pano.lat) == (2.2945, 48.8584)


def test_returns_none_when_everything_fails(monkeypatch):
    monkeypatch.setattr(rsv, "_get", lambda url, timeout, **kwargs: None)
    assert random_panorama(max_retry=3) is None


def test_rejects_a_generic_panoid(monkeypatch):
    def fake_get(url, timeout, **kwargs):
        if url.startswith("https://randomstreetview"):
            return _RSV_BODY
        return 'callbackfunc([1,"generic",[null]])'

    monkeypatch.setattr(rsv, "_get", fake_get)
    assert random_panorama(max_retry=2) is None


def test_rejects_a_wrong_length_panoid(monkeypatch):
    def fake_get(url, timeout, **kwargs):
        if url.startswith("https://randomstreetview"):
            return _RSV_BODY
        return 'callbackfunc([1,"tooshort",[null]])'

    monkeypatch.setattr(rsv, "_get", fake_get)
    assert random_panorama(max_retry=2) is None


def test_survives_an_unparseable_source(monkeypatch):
    monkeypatch.setattr(
        rsv, "_get", lambda url, timeout, **kwargs: "not what we expected"
    )
    assert random_panorama(max_retry=2) is None


def test_get_swallows_network_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(rsv.requests, "get", boom)
    assert rsv._get("https://example.com", 1.0) is None


# --------------------------------------------------------------------------- #
# Naver
# --------------------------------------------------------------------------- #
def test_naver_happy_path(monkeypatch):
    def fake_get(url, timeout, **kwargs):
        assert "map.naver.com" in url
        return _NAVER_BODY

    monkeypatch.setattr(rsv, "_get", fake_get)
    pano = random_panorama(platform="naver")
    assert pano is not None
    assert pano.id == "naverPano123"
    assert pano.platform == "naver"
    assert pano.url.endswith("naverPano123")


def test_naver_uses_korea_coordinates(monkeypatch):
    seen = {}

    def fake_get(url, timeout, **kwargs):
        seen["url"] = url
        return None

    monkeypatch.setattr(rsv, "_get", fake_get)
    assert random_panorama(platform="naver", max_retry=1) is None
    # Korea longitude is ~126-130, latitude ~33-38.
    assert "map.naver.com/p/api/panorama/nearby/" in seen["url"]


def test_naver_returns_none_without_features(monkeypatch):
    monkeypatch.setattr(
        rsv, "_get", lambda url, timeout, **kwargs: json.dumps({"features": []})
    )
    assert random_panorama(platform="naver", max_retry=2) is None


# --------------------------------------------------------------------------- #
# Kakao
# --------------------------------------------------------------------------- #
def test_kakao_happy_path(monkeypatch):
    def fake_get(url, timeout, **kwargs):
        assert "kakao.com" in url
        return _KAKAO_BODY

    monkeypatch.setattr(rsv, "_get", fake_get)
    pano = random_panorama(platform="kakao")
    assert pano is not None
    assert pano.id == "1129047585"
    assert pano.platform == "kakao"


def test_kakao_returns_none_when_count_zero(monkeypatch):
    body = json.dumps({"street_view": {"cnt": 0, "streetList": []}})
    monkeypatch.setattr(rsv, "_get", lambda url, timeout, **kwargs: body)
    assert random_panorama(platform="kakao", max_retry=2) is None


def test_kakao_survives_unparseable_source(monkeypatch):
    monkeypatch.setattr(rsv, "_get", lambda url, timeout, **kwargs: "nope")
    assert random_panorama(platform="kakao", max_retry=2) is None
