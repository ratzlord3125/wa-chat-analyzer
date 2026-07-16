# WhatsApp Chat Analyzer

Upload an exported WhatsApp chat and get a full statistics report — who talks the most, busiest
times, top words / emojis / links per person, response times, playful awards, and more. Everything
runs **in your browser**; your chat is never uploaded.

## 🔗 Live app

**Open it here → https://ratzlord3125.github.io/wa-chat-analyzer/**

No install needed — open the link, drop in your exported chat, and view or download the report.
*(After you enable GitHub Pages, replace the URL above with your actual page address.)*

## Using it

1. In WhatsApp, export a chat **without media** (iPhone: tap the chat name → **Export Chat** → **Without Media**; Android: **⋮ → More → Export chat → Without media**).
2. Open the live app and drag the `.zip` (or `.txt`) onto the page — or click **browse**.
3. Optionally set the **new-conversation gap**, **date order**, and **your name**.
4. Click **Analyze**, then **Download report (HTML)** to save the single file you can share in your group, or **Save as PDF**. *(Allow pop-ups for the site so Save as PDF and Open in new tab work.)*

## How it works

[Pyodide](https://pyodide.org) loads a full Python runtime in your browser and runs the same
`whatsapp_analyzer.py` engine on your uploaded file — the `read_chat_text → parse → analyze →
build_html` pipeline. The report is generated locally and shown right on the page. Your chat data
never leaves your device, and after the first visit the runtime is cached so it even works offline.

## Prefer the command line? Run the script directly on your PC

You don't need the website — `whatsapp_analyzer.py` is a complete standalone tool.

**Requirements:** Python 3.7+ (no packages to install). *Optional:* Microsoft Edge or Google Chrome
for the `--pdf` option (Edge is preinstalled on Windows).

**Run it:**

```
python whatsapp_analyzer.py "WhatsApp Chat with Friends.zip"
```

This writes `WhatsApp Chat with Friends_analysis.html` next to your input. Add `--pdf` to also
export a PDF.

**Options:**

| Option | Default | Description |
| --- | --- | --- |
| `input` *(required)* | — | The exported `.zip`, `_chat.txt`, or a folder containing it |
| `-o`, `--output` | `<input>_analysis.html` | Output HTML path |
| `--pdf` | *(off)* | Also export a PDF with everything expanded (uses Edge/Chrome, else saves a print-ready HTML) |
| `--date-order` | *auto* | Force date reading: `dmy`, `mdy`, or `ymd` |
| `--session-gap` | `180` *(3 hours)* | Minutes of silence that start a new conversation |
| `--me` | — | Your display name (optional) |

**Examples:**

```
python whatsapp_analyzer.py chat.zip -o report.html
python whatsapp_analyzer.py chat.zip --pdf
python whatsapp_analyzer.py chat.zip --date-order dmy --session-gap 120
```

If `--pdf` can't find Edge/Chrome, it saves `<name>_analysis_print.html` — open it and press
**Ctrl+P → Save as PDF**.

## Privacy

100% client-side. The only network requests are for the Pyodide runtime (from the jsDelivr CDN) and
the local `whatsapp_analyzer.py`. Your chat is never uploaded. After the first visit Pyodide is
cached, so it works offline.

## Notes

- **First load** downloads ~10 MB (the Python runtime); it's cached, so later visits are instant.
- **Very large chats** are parsed on the main thread and may briefly freeze the tab while working.
- **Save as PDF** uses the browser's print-to-PDF and needs pop-ups allowed for the site.
- Works on mobile browsers too, though they have less memory for very large exports.

## License

The [MIT License](https://choosealicense.com/licenses/mit/) is a good, simple open-source choice —
add a `LICENSE` file if you'd like.
