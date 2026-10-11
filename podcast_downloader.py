#!/usr/bin/env python3
"""
Download podcast episodes from any public RSS feed, and search for feeds by name.

Examples:
    python3 get_podcast.py malicious life                # search, pick a result, download
    python3 get_podcast.py malicious life --search       # just list matches + feed URLs
    python3 get_podcast.py https://example.com/feed.xml --dry-run
    python3 get_podcast.py <feed-url-or-file> --range 1 50
    python3 get_podcast.py <feed-url-or-file> --latest 10
    python3 get_podcast.py <feed-url-or-file> --name "My Show"
    python3 get_podcast.py <feed-url-or-file> -o ~/Music/Podcasts      # choose the folder
    python3 get_podcast.py <feed-url-or-file> -o /Volumes/Drive/Pods --flat

The output folder (-o/--out) is REQUIRED when downloading. It is not needed for
--search or --dry-run, which don't save anything.
Files go to  <folder>/<show name>/<show name> 001 - <title>.mp3
With --flat they go straight into <folder> with no show-name subfolder.
Safe to re-run: finished episodes are skipped, partial downloads use a .part file.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
HEADERS = {"User-Agent": "Mozilla/5.0 (podcast-downloader)"}


# ---------- networking ----------
def fetch(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def search_podcasts(term, limit=8):
    """Search Apple's free podcast directory; returns results that have a feed URL."""
    query = urllib.parse.urlencode(
        {"media": "podcast", "entity": "podcast", "limit": limit, "term": term}
    )
    with fetch("https://itunes.apple.com/search?" + query) as r:
        data = json.load(r)
    return [x for x in data.get("results", []) if x.get("feedUrl")]


def print_results(results):
    for i, r in enumerate(results, 1):
        print(f"{i}. {r.get('collectionName')} - {r.get('artistName')} "
              f"({r.get('trackCount', '?')} episodes)")
        print(f"   {r['feedUrl']}")


# ---------- feed parsing ----------
def to_int(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_duration(text):
    if not text:
        return 0
    try:
        secs = 0
        for part in text.strip().split(":"):
            secs = secs * 60 + int(float(part))
        return secs
    except ValueError:
        return 0


def load_feed(source):
    """source can be a feed URL or a feed XML file saved on disk."""
    if os.path.exists(source):
        root = ET.parse(source).getroot()
    else:
        with fetch(source) as r:
            root = ET.parse(r).getroot()

    show = (root.findtext("channel/title") or "Podcast").strip()
    items = []
    for item in root.iter("item"):
        enc = item.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        if (item.findtext(ITUNES + "episodeType") or "full").lower() == "trailer":
            continue
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate")).timestamp()
        except Exception:
            ts = 0
        items.append({
            "title": (item.findtext("title") or "untitled").strip(),
            "url": enc.get("url"),
            "type": (enc.get("type") or "").lower(),
            "size": to_int(enc.get("length")) or 0,
            "secs": parse_duration(item.findtext(ITUNES + "duration")),
            "ep": to_int(item.findtext(ITUNES + "episode")),
            "ts": ts,
        })
    return show, items


def assign_numbers(items):
    """Use the feed's episode numbers if they look reliable, else number by date."""
    have = [it["ep"] for it in items if it["ep"] is not None]
    if items and len(have) >= 0.8 * len(items) and len(set(have)) == len(have):
        numbered = [it for it in items if it["ep"] is not None]
        for it in numbered:
            it["num"] = it["ep"]
        dropped = len(items) - len(numbered)
        note = "feed episode numbers" + (f" ({dropped} unnumbered item(s) skipped)" if dropped else "")
        return sorted(numbered, key=lambda it: it["num"]), note
    items = sorted(items, key=lambda it: it["ts"])
    for i, it in enumerate(items, 1):
        it["num"] = i
    return items, "publish-date order (feed has no reliable episode numbers)"


# ---------- files ----------
def free_space(path):
    """Free bytes on the drive holding `path`, even if the folder doesn't exist yet."""
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return shutil.disk_usage(path).free


def safe_name(text, limit=100):
    text = re.sub(r'[\\/:*?"<>|]', "", text).strip().rstrip(".")
    return text[:limit].strip() or "untitled"


def extension(item):
    t, u = item["type"], item["url"].lower().split("?")[0]
    if "mp4" in t or "m4a" in t or u.endswith(".m4a"):
        return ".m4a"
    return ".mp3"


def show_progress(done, total, start):
    """Redraw a one-line progress bar: [#####     ]  42.0%  13.4/32.0 MB  5.2 MB/s"""
    speed = done / max(time.time() - start, 0.001) / 1e6
    if total:
        frac = min(done / total, 1)
        bar = "#" * int(frac * 30)
        line = (f"   [{bar:<30}] {frac * 100:5.1f}%  "
                f"{done / 1e6:6.1f}/{total / 1e6:.1f} MB  {speed:4.1f} MB/s")
    else:  # server didn't say how big the file is
        line = f"   {done / 1e6:6.1f} MB  {speed:4.1f} MB/s"
    sys.stdout.write("\r" + line)
    sys.stdout.flush()


def download(url, path, retries=3):
    tmp = path + ".part"
    for attempt in range(1, retries + 1):
        try:
            with fetch(url, timeout=60) as resp, open(tmp, "wb") as f:
                total = to_int(resp.headers.get("Content-Length")) or 0
                done = 0
                start = last = time.time()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if time.time() - last >= 0.2:  # redraw ~5 times a second
                        show_progress(done, total, start)
                        last = time.time()
                show_progress(done, total, start)
                sys.stdout.write("\n")
            os.replace(tmp, path)
            return True
        except Exception as e:
            sys.stdout.write("\n")
            print(f"   attempt {attempt}/{retries} failed: {e}")
            time.sleep(2 * attempt)
    return False


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Download podcasts from RSS feeds.")
    ap.add_argument("source", nargs="+",
                    help="feed URL, saved feed file, or words to search for")
    ap.add_argument("--search", action="store_true", help="only list search results")
    ap.add_argument("--dry-run", action="store_true",
                    help="show episode count and total size, download nothing")
    ap.add_argument("--range", nargs=2, type=int, metavar=("LO", "HI"),
                    help="only episodes LO..HI inclusive")
    ap.add_argument("--latest", type=int, metavar="N", help="only the newest N episodes")
    ap.add_argument("--name", help="show name for folder/filenames (default: feed title)")
    ap.add_argument("-o", "--out", metavar="FOLDER",
                    help="REQUIRED for downloading: folder to save into, "
                         "e.g. ~/Music/Podcasts (not needed for --search or --dry-run)")
    ap.add_argument("--flat", action="store_true",
                    help="save files directly in FOLDER, without a show-name subfolder")
    args = ap.parse_args()
    if not args.out and not (args.search or args.dry_run):
        ap.error("an output folder is required for downloading: add -o FOLDER "
                 "(e.g. -o ~/Music/Podcasts)")

    text = " ".join(args.source)
    if text.startswith(("http://", "https://")) or os.path.exists(text):
        feed = text
    else:
        results = search_podcasts(text)
        if not results:
            sys.exit("No podcasts found for that search.")
        print_results(results)
        if args.search:
            return
        choice = input("\nPick a number (Enter = 1, q = quit): ").strip().lower()
        if choice == "q":
            return
        idx = (to_int(choice) or 1) - 1
        if not 0 <= idx < len(results):
            sys.exit("Invalid choice.")
        feed = results[idx]["feedUrl"]
        print(f"Using feed: {feed}\n")

    show, items = load_feed(feed)
    items, how = assign_numbers(items)
    if args.range:
        items = [it for it in items if args.range[0] <= it["num"] <= args.range[1]]
    if args.latest:
        items = items[-args.latest:]

    name = safe_name(args.name or show)
    folder = None  # only set when -o was given (always the case when downloading)
    if args.out:
        out = os.path.abspath(os.path.expanduser(args.out))
        folder = out if args.flat else os.path.join(out, name)
    known = [it["size"] for it in items if it["size"] > 0]
    total = sum(known)
    hours = sum(it["secs"] for it in items) / 3600
    unknown = len(items) - len(known)

    print(f"Show: {name}")
    print(f"Saving to: {folder or '(not set - add -o FOLDER to download)'}")
    print(f"Episodes selected: {len(items)} (numbered by {how})")
    print(f"Total size: about {total / 1e9:.2f} GB"
          + (f" (+{unknown} episode(s) with unknown size)" if unknown else ""))
    if hours:
        print(f"Listening time: about {hours:.0f} hours")
    free = free_space(folder or ".")
    print(f"Free disk space: {free / 1e9:.1f} GB")
    if total > free:
        print("WARNING: not enough free space for all of this.")

    if args.dry_run or not items:
        if args.dry_run:
            print("\nDry run, nothing downloaded.")
        return

    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        sys.exit(f"Can't create the download folder '{folder}': {e}")
    existing = os.listdir(folder)
    for it in items:
        prefix = f"{name} {it['num']:03d} - "
        if any(f.startswith(prefix) and not f.endswith(".part") for f in existing):
            print(f"Skipping {it['num']:03d} (already downloaded)")
            continue
        path = os.path.join(folder, prefix + safe_name(it["title"]) + extension(it))
        print(f"Downloading {it['num']:03d}: {it['title']}")
        if not download(it["url"], path):
            print("   giving up on this one; re-run later to retry")
        time.sleep(1)  # be polite to the server
    print("Done.")


if __name__ == "__main__":
    main()
