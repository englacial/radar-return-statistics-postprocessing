"""Bundle the tool into one self-contained HTML file that runs from file://.

Inlines the stylesheet, both ES modules, and the gzipped data as base64. The
app checks for `globalThis.__INLINE_DATA__` and skips fetch() when it is set,
so the same app.js drives both the GitHub Pages build and this one.

    uv run python mission_design_tool/build_standalone.py
"""

import base64
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
SHEETS = ["antarctic", "greenland"]
# Concatenation order = dependency order (auto <- physics/warnings <- app).
MODULES = ["icons.js", "presets.js", "sidelobes.js", "auto.js", "physics.js",
           "warnings.js", "app.js"]


def strip_module_syntax(src: str) -> str:
    """Flatten an ES module into plain script scope (single-file concat)."""
    src = re.sub(r"^import .*?;\s*$", "", src, flags=re.MULTILINE)
    src = re.sub(r"^export (const|function|class|let|var)\b", r"\1", src, flags=re.MULTILINE)
    return src


def main():
    html = (HERE / "index.html").read_text()
    css = (HERE / "style.css").read_text()
    script = "\n".join(strip_module_syntax((HERE / m).read_text()) for m in MODULES)
    meta = json.loads((HERE / "data" / "meta.json").read_text())

    data = {"meta": meta}
    for sheet in SHEETS:
        blob = (HERE / "data" / f"{sheet}.bin.gz").read_bytes()
        data[sheet] = base64.b64encode(blob).decode()
    coast = HERE / "data" / "coast.json.gz"
    if coast.exists():
        data["coast"] = base64.b64encode(coast.read_bytes()).decode()

    html = html.replace('<link rel="stylesheet" href="style.css">', f"<style>\n{css}\n</style>")

    # Images must ride along as data URIs; a strict single file has no logos/ dir.
    for png in sorted((HERE / "logos").glob("*.png")):
        b64 = base64.b64encode(png.read_bytes()).decode()
        html = html.replace(f'src="logos/{png.name}"', f'src="data:image/png;base64,{b64}"')
    payload = json.dumps(data, separators=(",", ":"))
    html = html.replace(
        '<script type="module" src="app.js"></script>',
        f"<script>globalThis.__INLINE_DATA__ = {payload};</script>\n"
        f"<script>\n{script}\n</script>",
    )

    out = HERE / "dist"
    out.mkdir(exist_ok=True)
    path = out / "mission_design_tool.html"
    path.write_text(html)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
