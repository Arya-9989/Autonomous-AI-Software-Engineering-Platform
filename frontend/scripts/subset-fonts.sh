#!/usr/bin/env bash
# Regenerates src/fonts/*.woff2: Inter (opsz) and JetBrains Mono cut down to the characters the
# interface itself uses. src/index.css declares them after Fontsource's full Latin faces with a
# narrower unicode-range, so browsers use these small files for the UI and fetch the full ones
# only when a page shows other characters (an accented job title, say).
#
# Requires uv (fonttools runs in a throwaway environment; set UV="python -m uv" if uv is not
# on PATH). Run from frontend/:
#   bash scripts/subset-fonts.sh
set -euo pipefail

FONTTOOLS="fonttools[woff]==4.60.1"
# Printable ASCII plus the typography the UI uses: nbsp © · – — ‘ ’ “ ” … › ⌘ ✓
UNICODES="U+0020-007E,U+00A0,U+00A9,U+00B7,U+2013,U+2014,U+2018,U+2019,U+201C,U+201D,U+2026,U+203A,U+2318,U+2713"
# Kerning, ligatures and contextual forms, marks, localised forms, tabular figures, case forms.
FEATURES="kern,liga,calt,ccmp,locl,mark,mkmk,tnum,case,rlig"

subset() {
  ${UV:-uv} tool run --from "$FONTTOOLS" pyftsubset "$1" --unicodes="$UNICODES" \
    --layout-features="$FEATURES" --flavor=woff2 --output-file="$2"
  echo "$2: $(wc -c < "$2") bytes"
}

mkdir -p src/fonts
subset node_modules/@fontsource-variable/inter/files/inter-latin-opsz-normal.woff2 src/fonts/inter-ui-opsz.woff2
subset node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2 \
  src/fonts/jetbrains-mono-ui.woff2
