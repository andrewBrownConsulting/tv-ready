# Changelog

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
