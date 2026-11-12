# TV Ready

Converts videos in your media folders so they play cleanly on a TV: H.264 video, AAC audio, MKV/MP4,
and (optionally) a 16:9 frame with black bars for 4:3 material. Files that already qualify are left alone.

Open **TV Ready** in the sidebar to see what it's doing.

## Options

| Option | Default | What it does |
|---|---|---|
| `folders` | `/media` | Folders to convert (subfolders included). Your media folder is `/media`. |
| `backup_folder` | `/media/TV Ready originals` | Where originals are copied before being replaced. Leave empty to not keep originals. Never scanned. |
| `off_peak_start` / `off_peak_end` | `01:00` / `07:00` | Only convert inside this window (24 h clock, may cross midnight). Same value for both = any time. |
| `convert_watching_anytime` | `true` | With Jellyfin set up: convert what you're watching (in progress or up next) and the episode straight after it right away, even outside the window. |
| `pad_to_16_9` | `true` | Add black bars to make every picture 16:9, so TVs that fill the screen don't stretch it. |
| `quality` | `20` | x264 CRF. Lower = better quality and bigger files (18-23 is sensible). |
| `cpu_threads` | `2` | Encoder threads. Conversion always runs at the lowest CPU priority. |
| `jellyfin_url` | | e.g. `http://192.168.1.10:8096`. Optional. |
| `jellyfin_api_key` | | Jellyfin → Dashboard → API keys. |
| `jellyfin_user` | | User whose watching decides the order (name or id). Default: first user. |

## What gets converted

A file is converted when any of these is true:

- the video isn't H.264,
- the picture isn't 16:9 (and `pad_to_16_9` is on),
- the audio isn't AAC / AC-3 / E-AC-3,
- it isn't an MKV or MP4 file.

If only the audio or container is wrong, the video is copied as-is (fast, no quality loss).
`.mp4` files stay `.mp4`; everything else becomes `.mkv` (subtitle tracks are kept).

## The panel

- **Run now** - ignore the off-peak window until the queue is empty.
- **Pause / Resume** - freezes the current conversion where it is.
- **Skip** - abandon the current file (it's retried next time).
- **Retry failed** - try files that failed before again.

## Jellyfin

With a URL and API key, TV Ready:

1. converts files that are in progress or up next first - then the episodes after them, so autoplay lands on converted files - then anything added in the last two days;
2. remembers watched status and resume points before converting, and puts them back after Jellyfin
   rescans (a `.avi` that becomes `.mkv` is a new item to Jellyfin).

## Home Assistant events

TV Ready fires events you can use in automations:

- `tv_ready_started` - `{path, name, urgent}` when a file starts converting (`urgent` = being watched).
- `tv_ready_converted` - `{path, original_path, name, renamed, urgent}` when a converted file is in place.

For example, reload the video on your TV when the episode you're watching has just been converted, so the
new version (padded, seekable) takes over from where you are.
