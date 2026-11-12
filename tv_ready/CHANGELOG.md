# Changelog

## 0.3.2

- Skip now really skips: the file is left alone until TV Ready restarts or you press Retry failed / skipped.
- The file being converted no longer also shows at the top of Up next.

## 0.3.1

- Click a file in Up next to convert it next (even outside the off-peak window); click again to un-pin.
- Find box to search the whole queue.

## 0.3.0

- Uses Intel Quick Sync (hardware H.264 encoding) when available - much faster and lighter on the CPU. Detected automatically, falls back to software per file if needed; turn off with `hardware_acceleration: false`.

## 0.2.3

- "Up next" priority covers the next 3 episodes of each show you're watching (not the whole season), so newly added files aren't stuck behind it.

## 0.2.2

- Newly added files (last 2 days) are converted right after what you're watching, instead of waiting behind the whole library.

## 0.2.1

- Installs and updates now download a ready-made image (seconds) instead of building on your machine.

## 0.2.0

- Prebuilt images for amd64 and aarch64 (built by GitHub Actions).
- AppArmor security profile.
- Icon and logo.
- Friendly names in the panel ("Star Trek · S1E7 · What Are Little Girls Made Of").
- Converts the episode after the one you're watching straight away too, so autoplay lands on a converted file.
- Fires `tv_ready_started` / `tv_ready_converted` events in Home Assistant.
- Panel shows "Paused" while paused.

## 0.1.0

- First release: off-peak conversion to H.264/AAC with optional 16:9 padding, backups, Jellyfin priority and
  watch-state preservation, status panel.
