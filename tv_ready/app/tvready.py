"""TV Ready - converts a video library so every file plays cleanly on a TV.

Each video becomes H.264 + AAC in MKV/MP4 (optionally padded with black bars to a 16:9 frame,
because many TVs stretch 4:3 video to fill the screen). Files that already qualify are left alone.
Work happens in an off-peak window; with Jellyfin configured, whatever is being watched (or is up
next) can be converted straight away, and watch progress survives files being renamed.

Runs as a Home Assistant add-on: settings from /data/options.json, state in /data, web UI on the
ingress port. Standard library only (+ ffmpeg/ffprobe).
"""
import json, os, re, signal, subprocess, threading, time, urllib.parse, urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("TVREADY_DATA", "/data")
OPTIONS = os.path.join(DATA, "options.json")
STATE = os.path.join(DATA, "state.json")
SNAP = os.path.join(DATA, "watch_state.json")
PORT = 8099
EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".mpg", ".mpeg", ".flv", ".webm"}
SETTLE_SECONDS = 600  # ignore files modified in the last 10 min (still being copied in)


def load_options():
    try:
        return json.load(open(OPTIONS))
    except FileNotFoundError:  # running outside Home Assistant (development)
        return {"folders": ["/media"], "backup_folder": "", "off_peak_start": "01:00", "off_peak_end": "07:00",
                "convert_watching_anytime": False, "pad_to_16_9": True, "quality": 20, "cpu_threads": 2}


OPT = load_options()
LOCK = threading.RLock()
LOG = []


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    with LOCK:
        LOG.append(line)
        del LOG[:-300]


# ---------------------------------------------------------------- state

class State:
    def __init__(self):
        try:
            d = json.load(open(STATE))
        except (FileNotFoundError, ValueError):
            d = {}
        self.checked = d.get("checked", {})   # path -> "size:mtime" of files known to be TV-ready
        self.history = d.get("history", [])   # finished jobs, newest last
        self.failed = d.get("failed", {})     # path -> "size:mtime" that failed (retried if the file changes)
        self._pinned_saved = d.get("pinned", [])
        self.status = "starting"
        self.current = None                   # {file, pct, speed, eta, started, reason}
        self.queue = []
        self.queue_total = 0
        self.paused = False
        self.run_now = False                  # ignore the off-peak window until the queue is empty
        self.skip = False
        self.pinned = list(self._pinned_saved)  # files the user clicked "convert next" on, in click order
        self.proc = None
        self.wake = threading.Event()

    def save(self):
        with LOCK:
            tmp = STATE + ".tmp"
            json.dump({"checked": self.checked, "history": self.history[-200:], "failed": self.failed, "pinned": self.pinned}, open(tmp, "w"))
            os.replace(tmp, STATE)


S = State()


def sig(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


# ---------------------------------------------------------------- schedule

def in_off_peak(now=None):
    now = now or datetime.now()
    h1, m1 = map(int, OPT["off_peak_start"].split(":"))
    h2, m2 = map(int, OPT["off_peak_end"].split(":"))
    t, a, b = now.hour * 60 + now.minute, h1 * 60 + m1, h2 * 60 + m2
    if a == b:
        return True  # same start and end = always
    return a <= t < b if a < b else (t >= a or t < b)


# ---------------------------------------------------------------- Jellyfin (optional)

def jf_enabled():
    return bool(OPT.get("jellyfin_url") and OPT.get("jellyfin_api_key"))


def jf(path, body=None):
    req = urllib.request.Request(OPT["jellyfin_url"].rstrip("/") + path,
                                 headers={"Authorization": f'MediaBrowser Token="{OPT["jellyfin_api_key"]}"',
                                          "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET",
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read()
        return json.loads(t) if t else None


_jf_user = None


def jf_user():
    global _jf_user
    if _jf_user is None:
        users = jf("/Users")
        want = (OPT.get("jellyfin_user") or "").lower()
        match = [u for u in users if u["Name"].lower() == want or u["Id"] == want] or users
        _jf_user = match[0]["Id"]
    return _jf_user


def jf_hot_paths():
    """Files being watched or up next, most relevant first."""
    if not jf_enabled():
        return []
    try:
        u = jf_user()
        items = jf(f"/Users/{u}/Items/Resume?limit=20&fields=Path")["Items"]
        items += jf(f"/Shows/NextUp?userId={u}&limit=50&fields=Path")["Items"]
        return [i["Path"] for i in items if i.get("Path")]
    except Exception as e:
        log(f"Jellyfin unavailable ({e}) - converting in library order")
        return []


def jf_snapshot():
    """Remember watch state by path (minus extension) so renamed files (.avi -> .mkv) keep it."""
    if not jf_enabled():
        return
    try:
        snap = json.load(open(SNAP)) if os.path.exists(SNAP) else {}
        for i in jf(f"/Items?userId={jf_user()}&recursive=true&includeItemTypes=Movie,Episode,Video"
                    f"&fields=Path&enableUserData=true&limit=100000")["Items"]:
            ud, p = i.get("UserData", {}), i.get("Path")
            if p and (ud.get("Played") or ud.get("PlaybackPositionTicks")):
                snap[os.path.splitext(p)[0]] = {"Played": ud.get("Played", False), "Pos": ud.get("PlaybackPositionTicks", 0)}
        json.dump(snap, open(SNAP, "w"))
    except Exception as e:
        log(f"Couldn't save Jellyfin watch state: {e}")


def jf_refresh_and_restore():
    if not jf_enabled():
        return
    try:
        jf("/Library/Refresh", {})
        time.sleep(150)
        snap = json.load(open(SNAP)) if os.path.exists(SNAP) else {}
        u = jf_user()
        for i in jf(f"/Items?userId={u}&recursive=true&includeItemTypes=Movie,Episode,Video"
                    f"&fields=Path&enableUserData=true&limit=100000")["Items"]:
            s, ud = snap.get(os.path.splitext(i.get("Path") or "")[0]), i.get("UserData", {})
            if not s or ud.get("Played"):
                continue
            if s["Played"]:
                jf(f"/UserPlayedItems/{i['Id']}?userId={u}", {})
                log(f"Kept watched status: {i['Name']}")
            elif s["Pos"] and not ud.get("PlaybackPositionTicks"):
                jf(f"/UserItems/{i['Id']}/UserData?userId={u}", {"PlaybackPositionTicks": s["Pos"]})
                log(f"Kept resume point: {i['Name']}")
    except Exception as e:
        log(f"Couldn't restore Jellyfin watch state: {e}")


def ha_event(name, data):
    """Fire a Home Assistant event (e.g. so an automation can reload a video the moment it's converted)."""
    tok = os.environ.get("SUPERVISOR_TOKEN")
    if not tok:
        return
    try:
        req = urllib.request.Request(f"http://supervisor/core/api/events/{name}", data=json.dumps(data).encode(),
                                     headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        log(f"Couldn't notify Home Assistant ({name}): {e}")


# ---------------------------------------------------------------- hardware encoding (Intel Quick Sync via VA-API)

HW = None   # {"driver": "iHD"} when hardware H.264 encoding works


def detect_hw():
    """Encode a 1-second test clip with Quick Sync; use it only if that works."""
    global HW
    if not OPT.get("hardware_acceleration", True) or not os.path.exists("/dev/dri/renderD128"):
        return
    for drv in ("iHD", "i965"):
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-vaapi_device", "/dev/dri/renderD128", "-f", "lavfi",
                            "-i", "testsrc=size=640x360:rate=25:duration=1", "-vf", "format=nv12,hwupload",
                            "-c:v", "h264_vaapi", "-f", "null", "-"], capture_output=True, text=True,
                           env=dict(os.environ, LIBVA_DRIVER_NAME=drv))
        if r.returncode == 0:
            HW = {"driver": drv}
            log(f"Intel Quick Sync available ({drv} driver) - using hardware H.264 encoding")
            return
    log("No hardware encoder found - using software encoding")


# ---------------------------------------------------------------- media

def probe(f):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,width,height,sample_aspect_ratio:format=duration", "-of", "json", f],
                         capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    st = d.get("streams", [])
    v = next((s for s in st if s["codec_type"] == "video" and s.get("width")), None)
    a = next((s for s in st if s["codec_type"] == "audio"), None)
    dur = float(d.get("format", {}).get("duration") or 0)
    return v, a, dur


def plan(f):
    """None if the file already plays well, else a dict describing the conversion."""
    v, a, dur = probe(f)
    if not v:
        return None
    try:
        n, d = (int(x) for x in v.get("sample_aspect_ratio", "1:1").split(":"))
        sar = n / d if n and d else 1.0
    except ValueError:
        sar = 1.0
    dar = v["width"] * sar / v["height"]
    shape_ok = (1.70 <= dar <= 1.82) or not OPT["pad_to_16_9"]
    v_ok = v["codec_name"] == "h264" and shape_ok
    a_ok = a is None or a["codec_name"] in ("aac", "ac3", "eac3")
    ext_ok = os.path.splitext(f)[1].lower() in (".mkv", ".mp4")
    if v_ok and a_ok and ext_ok:
        return None
    why = []
    if v["codec_name"] != "h264": why.append(f"{v['codec_name']} video")
    if not shape_ok: why.append(f"{dar:.2f}:1 picture")
    if not a_ok: why.append(f"{a['codec_name']} audio")
    if not ext_ok: why.append(os.path.splitext(f)[1] + " file")
    return {"v_ok": v_ok, "a_ok": a_ok, "dur": dur, "why": ", ".join(why)}


VF_PAD = ("scale=trunc(iw*sar/2)*2:ih,setsar=1,"
          "pad=w=trunc(max(iw\\,ih*16/9)/2)*2:h=trunc(max(ih\\,iw*9/16)/2)*2:x=(ow-iw)/2:y=(oh-ih)/2:color=black")
VF_PLAIN = "scale=trunc(iw*sar/2)*2:ih,setsar=1"


def library_files():
    bak = os.path.abspath(OPT.get("backup_folder") or "/nonexistent") + os.sep
    files = []
    for root in OPT["folders"]:
        for d, dirs, fs in os.walk(root):
            if (os.path.abspath(d) + os.sep).startswith(bak):
                dirs[:] = []
                continue
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            files += [os.path.join(d, x) for x in fs if not x.startswith(".") and os.path.splitext(x)[1].lower() in EXTS]
    return files


def pending(files):
    out = []
    for f in files:
        try:
            s = sig(f)
            if S.checked.get(f) == s or S.failed.get(f) == s or time.time() - os.path.getmtime(f) < SETTLE_SECONDS:
                continue
        except FileNotFoundError:
            continue
        out.append(f)
    return out


def order(todo, hot):
    def rank(f):
        if f in S.pinned:
            return (-1, S.pinned.index(f), 0, f)
        if f in hot:
            return (0, hot.index(f), f)
        best = None  # the episodes right after a hot one in the same folder, closest first
        for n, h in enumerate(hot):
            if os.path.dirname(h) == os.path.dirname(f) and f > h:
                later = sorted(x for x in todo if os.path.dirname(x) == os.path.dirname(h) and x > h)
                best = min(best or (99, 99), (later.index(f) if f in later else 99, n))
        if best and best[0] < 3:  # only the next few episodes; the rest of the season waits its turn
            return (1, best[0], best[1], f)
        try:  # 3rd: newly added files (downloaded in the last 2 days), newest first - you probably want them soon
            age = time.time() - os.path.getmtime(f)
        except OSError:
            age = 1e9
        return (2, int(age), 0, f) if age < 2 * 86400 else (3, 0, 0, f)
    ranked = sorted(todo, key=rank)
    # "being watched" = the files themselves and the episode straight after each (so autoplay lands on a converted file)
    urgent = {f for f in todo if rank(f)[0] <= 0 or (rank(f)[0] == 1 and rank(f)[1] == 0)}
    return ranked, urgent


def convert(f, p, urgent, hw=None):
    """Returns True if the library file was replaced."""
    hw = HW if hw is None else (hw or None)
    is_mp4 = os.path.splitext(f)[1].lower() == ".mp4"
    out = os.path.splitext(f)[0] + (".mp4" if is_mp4 else ".mkv")
    tmp = os.path.join(os.path.dirname(f), ".tvready_" + os.path.basename(out))
    threads = str(max(OPT["cpu_threads"], 4) if urgent else OPT["cpu_threads"])
    vf = VF_PAD if OPT["pad_to_16_9"] else VF_PLAIN
    if p["v_ok"]:
        vargs, pre = ["-c:v", "copy"], []
    elif hw:   # decode + bars in software, compress on the Intel GPU
        vargs = ["-vf", vf + ",format=nv12,hwupload", "-c:v", "h264_vaapi", "-rc_mode", "CQP", "-qp", str(OPT["quality"] + 2)]
        pre = ["-vaapi_device", "/dev/dri/renderD128"]
    else:
        vargs, pre = ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", str(OPT["quality"]), "-threads", threads], []
    aargs = ["-c:a", "copy"] if p["a_ok"] else ["-c:a", "aac", "-b:a", "160k"]
    sargs = ["-sn"] if is_mp4 else ["-map", "0:s?", "-c:s", "copy"]
    cmd = ["nice", "-n", "5" if urgent else "19", "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *pre,
           "-i", f, "-map", "0:v:0", "-map", "0:a?", *vargs, *aargs, *sargs, "-progress", "pipe:1", "-nostats", tmp]
    env = dict(os.environ, LIBVA_DRIVER_NAME=hw["driver"]) if hw else None
    before = os.path.getsize(f)
    started = time.time()
    with LOCK:
        S.current = {"file": f, "pct": 0.0, "speed": "", "eta": None, "started": started,
                     "reason": p["why"], "urgent": urgent, "size_before": before,
                     "encoder": "Quick Sync" if (hw and not p["v_ok"]) else ("copy" if p["v_ok"] else "software")}
    S.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    for line in S.proc.stdout:
        k, _, val = line.strip().partition("=")
        with LOCK:
            if k == "out_time_us" and val.isdigit() and p["dur"]:
                S.current["pct"] = min(99.9, int(val) / 1e6 / p["dur"] * 100)
                el = time.time() - started
                if S.current["pct"] > 0.5:
                    S.current["eta"] = el / S.current["pct"] * (100 - S.current["pct"])
            elif k == "speed":
                S.current["speed"] = val
        if S.skip:
            S.proc.terminate()
    err = S.proc.stderr.read()
    rc = S.proc.wait()
    S.proc = None
    with LOCK:
        S.current = None
    if S.skip:
        S.skip = False
        if os.path.exists(tmp): os.remove(tmp)
        log(f"Skipped {pretty(f)}")
        return False
    if rc != 0:
        if os.path.exists(tmp): os.remove(tmp)
        if hw and not p["v_ok"]:
            log(f"Quick Sync couldn't convert {pretty(f)} ({err.strip()[-120:]}) - retrying in software")
            return convert(f, p, urgent, hw=False)
        S.failed[f] = sig(f)
        S.history.append({"file": f, "result": "failed", "at": time.time(), "why": p["why"], "error": err.strip()[-300:]})
        log(f"FAILED {pretty(f)}: {err.strip()[-200:]}")
        S.save()
        return False
    if OPT.get("backup_folder"):
        bak = os.path.join(OPT["backup_folder"], rel(f))
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        if not os.path.exists(bak):
            subprocess.run(["cp", "-p", f, bak], check=True)
    os.replace(tmp, out)
    if out != f:
        os.remove(f)
    after = os.path.getsize(out)
    S.checked[out] = sig(out)
    S.history.append({"file": out, "result": "converted", "at": time.time(), "why": p["why"],
                      "seconds": round(time.time() - started), "before": before, "after": after})
    log(f"Converted {pretty(out)} ({p['why']}) in {round((time.time() - started) / 60)} min")
    ha_event("tv_ready_converted", {"path": out, "original_path": f, "name": pretty(out), "renamed": out != f, "urgent": urgent})
    S.save()
    return out != f


def rel(f):
    for root in OPT["folders"]:
        if f.startswith(root.rstrip("/") + "/"):
            return f[len(root.rstrip("/")) + 1:]
    return f


JUNK = re.compile(r"\b(480p|576p|720p|1080p|2160p|4k|uhd|hdr|bluray|blu-ray|brrip|bdrip|webrip|web-dl|webdl|web|hdtv|dvdrip|"
                  r"x264|x265|h264|h\.264|h265|hevc|xvid|divx|aac|ac3|dts|remux|proper|repack|10bit)\b.*$", re.I)


def pretty(path):
    """Human-friendly name: 'Show · S1E7 · Title' for episodes, 'Title (Year)' for films."""
    r = rel(path)
    parts = r.split("/")
    base = os.path.splitext(parts[-1])[0]
    clean = lambda t: re.sub(r"\s+", " ", re.sub(r"[\[(][^\])]*[\])]", " ", t.replace("_", " ").replace(".", " "))).strip(" -·")
    m = re.search(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,3})", base)
    if m:
        show = parts[0] if len(parts) > 2 else clean(base[:m.start()])
        title = clean(JUNK.sub("", base[m.end():].replace(".", " ")))
        title = re.sub(r"\s+-\s+", ", ", title)
        return " · ".join(x for x in (show, f"S{int(m.group(1))}E{int(m.group(2))}", title) if x)
    y = re.search(r"[ .(\[]((?:19|20)\d\d)[ .)\]]", base + " ")
    title = clean(JUNK.sub("", (base[:y.start()] if y else base).replace(".", " ")))
    return f"{title} ({y.group(1)})" if y else title or parts[-1]


# ---------------------------------------------------------------- worker

def worker():
    log(f"TV Ready started. Folders: {', '.join(OPT['folders'])}. Off-peak {OPT['off_peak_start']}-{OPT['off_peak_end']}."
        + (" Jellyfin connected." if jf_enabled() else ""))
    snapshot_done = False
    renamed_since_refresh = False
    while True:
        files = library_files()
        todo = pending(files)
        hot = jf_hot_paths() if todo else []
        todo, hot_todo = order(todo, hot)
        with LOCK:
            S.queue, S.queue_total = todo[:1000], len(todo)
        if not todo:
            if renamed_since_refresh:
                jf_refresh_and_restore(); renamed_since_refresh = False
            S.run_now = False
            S.status = "idle"
            S.wake.wait(600); S.wake.clear()
            continue
        f = todo[0]
        urgent = (f in hot_todo and OPT.get("convert_watching_anytime")) or f in S.pinned
        if S.paused:
            S.status = "paused"; S.wake.wait(30); S.wake.clear(); continue
        if not (in_off_peak() or S.run_now or urgent):
            S.status = "waiting"
            S.wake.wait(60); S.wake.clear()
            continue
        S.status = "converting"
        try:
            p = plan(f)
        except Exception as e:
            log(f"Couldn't read {rel(f)}: {e}"); S.failed[f] = sig(f); continue
        if p is None:
            S.checked[f] = sig(f); S.save(); continue
        if not snapshot_done:
            jf_snapshot(); snapshot_done = True
        log(f"Converting {pretty(f)} - {p['why']}" + (" (being watched)" if urgent else ""))
        ha_event("tv_ready_started", {"path": f, "name": pretty(f), "urgent": bool(urgent)})
        try:
            if f in S.pinned:
                S.pinned.remove(f)
            if convert(f, p, urgent):
                renamed_since_refresh = True
                if jf_enabled():
                    threading.Thread(target=jf_refresh_and_restore, daemon=True).start()
                    renamed_since_refresh = False
        except Exception as e:
            log(f"Error converting {rel(f)}: {e}")
            S.failed[f] = sig(f) if os.path.exists(f) else ""


# ---------------------------------------------------------------- web UI / API

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ok(self, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _allowed(self):
        # Only Home Assistant's ingress proxy may talk to us.
        if os.path.exists(OPTIONS) and self.client_address[0] not in ("172.30.32.2", "127.0.0.1"):
            self.send_error(403)
            return False
        return True

    def do_GET(self):
        if not self._allowed():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._ok(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), "rb").read(), "text/html; charset=utf-8")
        if path == "/api/status":
            with LOCK:
                cur = dict(S.current) if S.current else None
                if cur: cur["name"], cur["file"] = pretty(cur["file"]), rel(cur["file"])
                hist = [dict(h, name=pretty(h["file"]), file=rel(h["file"])) for h in S.history[-30:]][::-1]
                conv = [h for h in S.history if h["result"] == "converted"]
                body = {
                    "status": "paused" if S.paused else S.status, "paused": S.paused, "run_now": S.run_now,
                    "off_peak": in_off_peak(), "window": [OPT["off_peak_start"], OPT["off_peak_end"]], "hw": bool(HW),
                    "current": cur, "queue": [{"name": pretty(f), "file": rel(f), "pinned": f in S.pinned} for f in S.queue], "queue_total": S.queue_total,
                    "history": hist, "log": LOG[-80:][::-1], "jellyfin": jf_enabled(),
                    "stats": {"converted": len(conv), "ready": len(S.checked),
                              "failed": len([h for h in S.history if h["result"] == "failed"]),
                              "bytes_before": sum(h.get("before", 0) for h in conv),
                              "bytes_after": sum(h.get("after", 0) for h in conv)},
                }
            return self._ok(json.dumps(body))
        self.send_error(404)

    def do_POST(self):
        if not self._allowed():
            return
        path = self.path.split("?")[0]
        if path == "/api/pause":
            S.paused = True
            if S.proc: S.proc.send_signal(signal.SIGSTOP)
            log("Paused")
        elif path == "/api/resume":
            S.paused = False
            if S.proc: S.proc.send_signal(signal.SIGCONT)
            log("Resumed")
        elif path == "/api/run_now":
            S.run_now = not S.run_now
            log("Running now, ignoring the off-peak window" if S.run_now else "Back to the off-peak schedule")
        elif path == "/api/skip":
            S.skip = True
            if S.proc and S.paused: S.proc.send_signal(signal.SIGCONT)
        elif path == "/api/pin":
            relf = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("file", [""])[0]
            full = next((f for f in S.queue if rel(f) == relf), None)
            if full:
                if full in S.pinned:
                    S.pinned.remove(full); log(f"Un-pinned {pretty(full)}")
                else:
                    S.pinned.append(full); log(f"Pinned {pretty(full)} - converts next")
                S.save()
        elif path == "/api/retry_failed":
            S.failed.clear(); S.save(); log("Retrying failed files")
        else:
            return self.send_error(404)
        S.wake.set()
        self._ok('{"ok":true}')


def main():
    # clean up half-written files from a previous run
    for root in OPT["folders"]:
        for d, _, fs in os.walk(root):
            for x in fs:
                if x.startswith(".tvready_"):
                    try: os.remove(os.path.join(d, x))
                    except OSError: pass
    detect_hw()
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
