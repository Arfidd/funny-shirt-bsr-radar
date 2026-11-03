"""Build the Blogger deliverables from blogger/src/.

    python blogger/build.py                                   # uses blogger/config.json
    python blogger/build.py --data-url https://raw.githubusercontent.com/me/bsr/main/data/bsr_shirts.json

Outputs (blogger/dist/):
    bsr-dashboard-theme.xml  full Blogger theme (Theme > Edit HTML > replace all)
    bsr-gadget.html          HTML/JavaScript gadget for an existing theme
    preview.html             local preview reading ../../data/bsr_shirts.json
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
DIST = HERE / "dist"

FONTS = (
    "<link href='https://fonts.googleapis.com' rel='preconnect'/>\n"
    "  <link crossorigin='anonymous' href='https://fonts.gstatic.com' rel='preconnect'/>\n"
    "  <link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&amp;display=swap' rel='stylesheet'/>"
)

# Page chrome used only by the full theme (the gadget inherits the host theme).
THEME_CSS = """
/* ---------- Theme shell ---------- */
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: #f6f7f9; color: #14171f; font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
@media (prefers-color-scheme: dark) { body { background: #0e1015; color: #eef0f4; } }
.bsr-shell { max-width: 1320px; margin: 0 auto; padding: 12px 16px 32px; }
.bsr-blogbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 4px 10px; font-size: 13px; }
.bsr-blogbar a { color: inherit; opacity: .7; text-decoration: none; font-weight: 600; }
.bsr-blogbar a:hover { opacity: 1; }
#bsr-app { padding: 8px 0 0; background: transparent; border-radius: 0; }
.bsr-home .bsr-posts { display: none; }
.bsr-item #bsr-app { display: none; }
.bsr-posts { max-width: 760px; margin: 0 auto; line-height: 1.65; font-size: 16px; }
.bsr-posts a { color: #ff7a1a; }
.bsr-posts .post-title, .bsr-posts h3.post-title { font-size: 28px; line-height: 1.25; margin: 12px 0; }
.bsr-posts img, .bsr-posts iframe { max-width: 100%; height: auto; }
.bsr-posts .post-footer, .bsr-posts .blog-pager { font-size: 13px; opacity: .75; }
@media (max-width: 640px) { .bsr-shell { padding: 8px 16px 24px; } #bsr-app { padding: 4px 0 0; } }
"""


def js_config(cfg):
    return "window.BSR_CONFIG = " + json.dumps(cfg, indent=2) + ";"


def theme_xml(css, markup, js, cfg):
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE html>
<html b:css='false' b:defaultwidgetversion='2' b:layoutsVersion='3' b:responsive='true' b:templateVersion='1.0.0' b:version='3' expr:dir='data:blog.languageDirection' lang='en' xmlns='http://www.w3.org/1999/xhtml' xmlns:b='http://www.google.com/2005/gml/b' xmlns:data='http://www.google.com/2005/gml/data' xmlns:expr='http://www.google.com/2005/gml/expr'>
<head>
  <meta content='width=device-width, initial-scale=1' name='viewport'/>
  <meta content='#0e1015' name='theme-color'/>
  <b:include data='blog' name='all-head-content'/>
  <title><data:view.title.escaped/></title>
  {FONTS}
<b:skin version='1.0.0'><![CDATA[
{css}
{THEME_CSS}
]]></b:skin>
</head>
<body expr:class='data:view.isSingleItem ? &quot;bsr-item&quot; : &quot;bsr-home&quot;'>
<div class='bsr-shell'>
<nav class='bsr-blogbar'><a expr:href='data:blog.homepageUrl'><data:blog.title/></a></nav>
{markup}
<main class='bsr-posts'>
<b:section class='main' id='main' showaddelement='yes'>
<b:widget id='Blog1' locked='true' title='Blog Posts' type='Blog' version='2' visible='true'/>
</b:section>
</main>
</div>
<script>//<![CDATA[
/* ===== EDIT ME: point dataUrl at your repo's raw JSON ===== */
{js_config(cfg)}
//]]></script>
<script>//<![CDATA[
{js}
//]]></script>
</body>
</html>
"""


def gadget_html(css, markup, js, cfg):
    return f"""<!-- BSR Radar gadget: Layout > Add a Gadget > HTML/JavaScript, paste everything below. -->
<style>
{css}
</style>
{markup}
<script>
/* ===== EDIT ME: point dataUrl at your repo's raw JSON ===== */
{js_config(cfg)}
</script>
<script>
{js}
</script>
"""


def preview_html(css, markup, js, cfg):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BSR Radar Preview</title>
{FONTS.replace("&amp;", "&")}
<style>
{css}
{THEME_CSS}
</style>
</head>
<body class="bsr-home">
<div class="bsr-shell">
<nav class="bsr-blogbar"><a href="#">Local preview</a></nav>
{markup}
</div>
<script>{js_config(cfg)}</script>
<script>
{js}
</script>
</body>
</html>
"""


def validate_theme(text, js, css):
    for name, body in (("dashboard.js", js), ("dashboard.css", css)):
        if "]]>" in body:
            sys.exit(f"{name} contains ']]>' which would end the CDATA block")
    try:
        ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        sys.exit(f"theme XML is not well-formed: {exc}")
    # Blogger rewrites empty non-void tags as <tag/>, which browsers misparse.
    empty = re.findall(r"<(div|span|p|section|a|button|label|main|nav)\b[^>]*>\s*</\1>", text)
    if empty:
        sys.exit(f"empty non-void tags in theme markup: {empty}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-url", help="raw GitHub URL of data/bsr_shirts.json (overrides config.json)")
    ap.add_argument("--preview-data", default="../../data/bsr_shirts.json", help="JSON path preview.html loads")
    args = ap.parse_args(argv)

    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8-sig"))  # tolerate editors that add a BOM
    if args.data_url:
        cfg["dataUrl"] = args.data_url
    css = (SRC / "dashboard.css").read_text(encoding="utf-8").strip()
    markup = (SRC / "dashboard.html").read_text(encoding="utf-8").strip()
    js = (SRC / "dashboard.js").read_text(encoding="utf-8").strip()

    DIST.mkdir(exist_ok=True)
    theme = theme_xml(css, markup, js, cfg)
    validate_theme(theme, js, css)
    outputs = {
        "bsr-dashboard-theme.xml": theme,
        "bsr-gadget.html": gadget_html(css, markup, js, cfg),
        "preview.html": preview_html(css, markup, js, {**cfg, "dataUrl": args.preview_data}),
    }
    for name, text in outputs.items():
        (DIST / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote blogger/dist/{name} ({len(text.encode('utf-8')) // 1024} KB)")
    if "YOUR_GITHUB_USER" in cfg["dataUrl"]:
        print("note: dataUrl is still the placeholder - pass --data-url or edit blogger/config.json")


if __name__ == "__main__":
    main()
