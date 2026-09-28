# Vendored frontend libraries

Locally pinned (no CDN dependency), served from `/static/vendor/*` (see
`app/main.py`'s `StaticFiles` mount). Same convention as signal-copier's
own `app/static/vendor/README.md` -- copied verbatim from that repo's
pinned build, not re-fetched, so the version stays identical across
both services.

| File | Package | Pinned version |
|---|---|---|
| `chart.umd.min.js` | [chart.js](https://github.com/chartjs/Chart.js) | 4.5.1 |

Used by PU-03's "Try our fit simulator" equity-curve chart
(`app/templates/pu03_portfolio_detail.html`) -- the only chart anywhere
in this app as of its introduction.
