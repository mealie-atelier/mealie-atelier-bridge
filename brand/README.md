# Bridge brand assets

The Week Dial mark, Bridge variant: a plate seen from above, its rim notched into seven day sections (today in paprika), with a three-arch bridge on the plate. The masters live in the `mealie-atelier/.github` repository under `brand/` (palette, clear space and usage rules are in its [`brand/README.md`](https://github.com/mealie-atelier/.github/tree/main/brand)). The files here are generated copies; change the masters there, never edit these by hand.

- `icon.svg` is the app tile master (night tile, cream plate). `icon-192.png`, `icon-512.png` and `icon-1024.png` are PNG exports of it.
- `social-preview.png` is the 1280 × 640 GitHub social preview and `social-preview.jpg` the upload version (both well under 1 MB). Both are byte-identical copies of `brand/social/mealie-atelier-bridge-social-preview.*` in the `.github` repository, where they are generated; do not edit them here.
- The Home Assistant brand files live in `custom_components/mealie_atelier/brand/` and are read by Home Assistant from there: `icon.png` (256) and `icon@2x.png` (512) on a transparent square for light UI, `dark_icon*.png` for dark UI, and the landscape `logo*.png` and `dark_logo*.png` lockups (shortest side 128 and 256).
