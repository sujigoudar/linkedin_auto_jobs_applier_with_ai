# Vendored frontend libraries

Locally pinned (no CDN dependency), served from `/static/vendor/*` (see
`app/main.py`'s `StaticFiles` mount).

| File | Package | Pinned version |
|---|---|---|
| `chart.umd.min.js` | [chart.js](https://github.com/chartjs/Chart.js) | 4.5.1 |
| `tabulator.min.js` | [tabulator-tables](https://github.com/tabulator-tables/tabulator) | 6.5.3 |
| `tabulator.min.css` | tabulator-tables | 6.5.3 |

Fetched via `npm pack <package>@<major>` from the public npm registry and
extracted from each tarball's own `dist/`. To upgrade, repeat that for the
new version, diff the changelog for breaking changes, and re-test the
dashboard's chart/table rendering before replacing these files.
