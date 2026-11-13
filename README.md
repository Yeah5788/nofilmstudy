# NoFilmStudy

Live sports board, sharp-money splits and quant picks. Public site: https://yeah5788.github.io/nofilmstudy/

- `index.html` + `data.json` are the published site (GitHub Pages, root of `main`).
- `tools/app.html` is the app source; `tools/build_site.py <app.html> <db-dump-dir> <out-dir>` rebuilds the site from a database dump.
- An hourly scheduled task exports the latest data and pushes it here.

Picks are for information only, not financial advice.
