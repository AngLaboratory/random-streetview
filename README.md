# random-streetview

[![PyPI](https://img.shields.io/pypi/v/random-streetview)](https://pypi.org/project/random-streetview/)
[![Python](https://img.shields.io/pypi/pyversions/random-streetview)](https://pypi.org/project/random-streetview/)

One call, one random Google Street View panorama.

```bash
pip install random-streetview
```

```python
from random_streetview import random_panorama

pano = random_panorama()

pano.id     # 'ptFCsB5aFyaWDDlpbCliVw'
pano.lon    # 2.2954822
pano.lat    # 48.8583758
pano.url    # 'https://www.google.com/maps/@?api=1&map_action=pano&pano=...'
```

`Panorama` is a named tuple, so `panoid, lon, lat = random_panorama()` works too.

Nothing raises on a network or parsing failure — you get `None` once the
attempts are used up.

## Timeouts

```python
random_panorama(max_retry=10, timeout=3.0)   # the defaults
```

`timeout` is per request, in seconds, and is raised to **0.5** if you ask for
less. A single call budgets at most **30 seconds** of timeout across its
attempts, so `max_retry` is an upper bound rather than a promise: at
`timeout=10.0` you get 3 attempts, not 10. The default pairing is sized to
reach all 10. Time actually spent on the wire does not count against the
budget — only the timeouts do.

## How it works, and why it can break

Picking a coordinate at random mostly lands you in the ocean, so candidates
come from [randomstreetview.com][rsv] and [wandery.it][wandery], which keep
lists of places known to have coverage. Each candidate is then resolved to a
panorama id through `GeoPhotoService.SingleImageSearch`, an **undocumented
internal Google Maps endpoint**.

That means no API key — and no guarantees. The scraped sites will change their
HTML, and Google can alter or start refusing that endpoint at any time; when
that happens this library returns `None` rather than breaking your program. If
you need something dependable, use the official
[Street View Static API metadata endpoint][meta] with a key instead (metadata
requests are not billed).

Requests are not rate-limited for you. Space out your calls.

## Development

```bash
uv sync
uv run pytest    # fully offline; network calls are monkeypatched
```

## License

MIT

[rsv]: https://randomstreetview.com/
[wandery]: https://www.wandery.it/
[meta]: https://developers.google.com/maps/documentation/streetview/metadata
