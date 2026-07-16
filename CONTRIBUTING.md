# Contributing

Thanks for your interest in improving the WhatsApp Chat Analyzer! This is a small, friendly
open-source project — bug reports, ideas, and pull requests are all welcome.

## Ground rules (two invariants)

Two things keep this project simple and trustworthy. Please preserve both:

1. **`whatsapp_analyzer.py` uses the Python standard library only** — no `pip install`
   dependencies. This is exactly what lets the *same file* run in the browser via Pyodide. If you
   find yourself reaching for a third-party package, there's almost always a stdlib way here.
2. **The generated report stays a single, self-contained, offline file** — no external scripts,
   fonts, images, or network calls in the HTML. Charts are hand-written inline SVG for this reason.
   Nothing the tool produces should ever upload data or "phone home"; privacy is the whole point.

## How the code is organized

Everything is a four-stage pipeline (the big comment block at the top of `whatsapp_analyzer.py`
has the full architecture and the per-message data schema):

```
read_chat_text()  ->  raw chat text (+ a display name)
parse()           ->  list of message dicts + meta
analyze()         ->  one stats dict `S`
build_html()      ->  the final self-contained HTML string
```

- `index.html` is the web app: it loads Pyodide, imports the script **unchanged**, and calls those
  same functions in the browser.
- `main()` is just the command-line wrapper around the pipeline.

## Run it locally

**Command line** (fastest for engine changes):

```
python whatsapp_analyzer.py path/to/chat.zip
python whatsapp_analyzer.py path/to/chat.zip --pdf
```

**Web app** (you must serve it — opening `index.html` directly is blocked by the browser):

```
cd whatsapp-analyzer-web
python -m http.server 8000
# then open http://localhost:8000
```

## Where to make common changes

| You want to... | Do this |
| --- | --- |
| Add a new statistic | Compute it in `analyze()` (store it in `S`), then render it in `build_html()`. |
| Add a new chart | Write a small `svg_*` helper that returns an SVG string, and call it from `build_html()`. |
| Add a new award | Add one `mk(...)` call in `compute_awards()` — it builds the full ranking for you. |
| Support a new export/locale | Adjust the regexes in `parse()` and the logic in `detect_date_order()`; add placeholder strings to the constants near the top and to `classify()`. |
| Tune word stats | Edit the `STOPWORDS` set near the top (English + common Hinglish today). |

## Testing (no formal suite yet)

Please sanity-check before opening a PR:

1. Run the script on a real export — or a small synthetic `_chat.txt` in **both** the iOS
   (`[2024-03-15, 10:30:45 PM] Name: hi`) and Android (`15/03/2024, 22:30 - Name: hi`) formats.
2. Open the generated HTML and confirm it renders and the numbers look right.
3. Check a **text-only** export (no media) and a **group** chat.
4. If you touched parsing, try a different `--date-order` and a chat with multi-line messages.

## Good first issues / ideas

- A small **unit-test suite** for `parse()` / `analyze()` using fixture chat logs (very welcome!).
- More **languages** in `STOPWORDS`, or smarter tokenization.
- **Translate the UI** (`index.html`) or the report labels (i18n).
- A **real progress bar** for the Pyodide download instead of the indeterminate one.
- Optional **word cloud** or **sentiment-over-time** section.
- **CSV / JSON export** of the raw stats alongside the HTML.
- A **dark mode** for the report.
- Better **view-once / media-type** detection as WhatsApp's export format evolves.

## Pull requests

1. Fork the repo and create a branch: `git checkout -b my-change`.
2. Make your change, keeping the two invariants above.
3. Test locally (see above).
4. Open a PR with a short description of *what* and *why*. Screenshots of the report help for UI changes.

Be kind and constructive in issues and reviews. Thank you for contributing! 🙌
