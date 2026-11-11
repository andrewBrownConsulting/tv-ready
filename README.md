# TV Ready

A Home Assistant add-on that converts your video library overnight so every file plays cleanly on your TV.

Many TVs (DLNA players, older smart TVs) choke on parts of a typical library: DivX/XviD `.avi` files, odd audio
codecs, and old 4:3 shows that get stretched to fill a widescreen. They also can't seek in a stream a server
is converting on the fly. TV Ready fixes the files once, so they play directly and seek instantly.

- **Converts to H.264 + AAC** in MKV/MP4 - plays on practically every TV.
- **Pads 4:3 and other shapes to 16:9** with black bars, so TVs that always fill the screen don't stretch them (optional).
- **Leaves good files alone** - only files that need it are touched.
- **Off-peak only** - converts in a time window you choose (01:00-07:00 by default), with a *Run now* button.
- **Keeps your originals** in a backup folder (optional).
- **Jellyfin-aware (optional)** - converts what you're watching and what's up next first, even outside the
  window, and keeps watched status / resume points when a file is renamed (`.avi` → `.mkv`).
- **Status panel in the sidebar** - live progress, speed and time left, the queue, finished files, size
  change, log, and pause / skip / run-now controls.

## Install

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FandrewBrownConsulting%2Ftv-ready)

Or: **Settings → Add-ons → Add-on store → ⋮ → Repositories**, add `https://github.com/andrewBrownConsulting/tv-ready`,
then install **TV Ready**, set your folders on the **Configuration** tab and start it.

See [the documentation](tv_ready/DOCS.md) for every option.

## Good to know

- Conversion is CPU-heavy. It runs at the lowest priority, but on small machines keep `cpu_threads` low and
  the off-peak window at night.
- Converted 4:3 files are padded permanently (the originals are kept if `backup_folder` is set).
- Files modified in the last 10 minutes are skipped, so it won't grab something still being copied in.
