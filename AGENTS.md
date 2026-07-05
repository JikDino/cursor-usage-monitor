# AGENTS.md

## Cursor Cloud specific instructions

This repository is a static site with **no build system, package manager, dependencies, tests, or lint setup**. It contains:

- `README.md` — describes the "Cursor Usage Monitor" Windows app (the compiled app is distributed via GitHub Releases, not as source in this repo).
- `blog-ad.html` — a static HTML ad page (300x250 ad slot) that loads an external ad script.

### Running the site

There is nothing to install. Serve the static files with any static HTTP server, e.g.:

```
python3 -m http.server 8000
```

Then open `http://localhost:8000/blog-ad.html`.

Notes:
- The ad slot loads an external script from `highperformanceformat.com`; it renders an empty 300x250 slot when the external ad does not load (e.g. restricted network). The page structure still renders correctly.
- There are no automated tests, lint, or build commands for this repo.
