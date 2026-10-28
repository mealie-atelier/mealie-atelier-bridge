# Bridge brand assets

Bridge uses the same original pasta-and-tomato photograph as Cards, with a distinct wordmark and a crisp geometric home connection mark. Its square, full-bleed composition uses straight edges, a restrained gold rule, and the shared aubergine palette.

- `icon.svg` is the self-contained master; its source photo is embedded so common SVG renderers produce the same result.
- `icon-192.png`, `icon-512.png` and `icon-1024.png` are ready-to-use exports.
- `social-preview.svg` is the editable artwork; `social-preview.png` is the 1280 × 640 export, and `social-preview.jpg` is the optimized GitHub upload (under 1 MB).
- `CREDITS.md` records the photograph attribution and source license.

Regenerate the square icon exports on macOS with the built-in `sips` tool:

```sh
for size in 192 512 1024; do
  sips -s format png -z "$size" "$size" icon.svg --out "icon-$size.png"
done
sips -s format png social-preview.svg --out social-preview.png
sips -s format jpeg -s formatOptions 86 social-preview.png --out social-preview.jpg
```
