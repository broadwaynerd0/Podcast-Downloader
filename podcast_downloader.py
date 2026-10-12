#!/usr/bin/env python3
"""
Download podcast episodes from any public RSS feed, and search for feeds by name.

Examples:
    python3 podcast_downloader.py malicious life                # search, pick a result, download
    python3 podcast_downloader.py malicious life --search       # just list matches + feed URLs
    python3 podcast_downloader.py https://example.com/feed.xml --dry-run
    python3 podcast_downloader.py <feed-url-or-file> --range 1 50
    python3 podcast_downloader.py <feed-url-or-file> --latest 10
    python3 podcast_downloader.py <feed-url-or-file> --name "My Show"
    python3 podcast_downloader.py <feed-url-or-file> -o ~/Music/Podcasts      # choose the folder
    python3 podcast_downloader.py <feed-url-or-file> -o /Volumes/Drive/Pods --flat

The output folder (-o/--out) is REQUIRED when downloading. It is not needed for
--search or --dry-run, which don't save anything.
Files go to  <folder>/<show name>/<show name> 001 - <title>.mp3
With --flat they go straight into <folder> with no show-name subfolder.
Safe to re-run: finished episodes are skipped, partial downloads use a .part file.
"""
# Everything below comes from Python's standard library, so there is nothing to pip install.
import argparse                      # reads the command-line options (-o, --dry-run, ...)
import json                          # decodes Apple's search results (JSON)
import os                            # file and folder paths
import re                            # regular expressions, used to clean filenames
import shutil                        # disk-space check
import sys                           # exit codes and writing to the terminal
import time                          # sleeping between downloads, measuring speed
import urllib.parse                  # builds the search URL safely
import urllib.request                # downloads feeds and episodes
import xml.etree.ElementTree as ET   # parses the RSS (XML) feed
from email.utils import parsedate_to_datetime   # RSS dates use the email date format

# RSS feeds put podcast-specific fields (episode number, duration, ...) in an "itunes"
# XML namespace. In ElementTree a namespaced tag is written as "{namespace-url}tag",
# so we keep the prefix here and glue it onto tag names later (ITUNES + "episode").
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"

# Some servers refuse requests that don't look like a browser, so we send a User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (podcast-downloader)"}


# ---------- networking ----------
def fetch(url, timeout=30):
    # Open a URL with our headers. The timeout stops a stalled server from hanging us forever.
    # The result works as a file-like object, so callers use it in a `with` block.
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def search_podcasts(term, limit=8):
    """Search Apple's free podcast directory; returns results that have a feed URL."""
    # urlencode escapes spaces and special characters so the query is a valid URL.
    query = urllib.parse.urlencode(
        {"media": "podcast", "entity": "podcast", "limit": limit, "term": term}
    )
    with fetch("https://itunes.apple.com/search?" + query) as r:
        data = json.load(r)  # parse the JSON response into Python dicts/lists
    # Keep only results that actually include an RSS address (feedUrl); we can't download without it.
    return [x for x in data.get("results", []) if x.get("feedUrl")]


def print_results(results):
    # Show a numbered list: "1. Show - Author (N episodes)" and its feed URL underneath.
    # enumerate(..., 1) makes the numbering start at 1 instead of 0.
    for i, r in enumerate(results, 1):
        print(f"{i}. {r.get('collectionName')} - {r.get('artistName')} "
              f"({r.get('trackCount', '?')} episodes)")
        print(f"   {r['feedUrl']}")


# ---------- feed parsing ----------
def to_int(text):
    # Turn text like " 42 " into the number 42. Returns None for missing or non-numeric
    # values (None, "", "abc") instead of crashing, so callers can just test `is None`.
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_duration(text):
    # `text` is the string from a feed's <itunes:duration> tag, or None if the tag is missing.
    # Feeds write it as "1:02:03" (h:m:s), "35:20" (m:s) or plain seconds like "2100".
    if not text:
        return 0  # no duration given
    try:
        secs = 0
        # Fold each ":"-separated part into one running total in base 60:
        # "1:02:03" -> ((0*60+1)*60+2)*60+3 = 3723 seconds.
        # int(float(...)) also tolerates decimals like "35.5".
        for part in text.strip().split(":"):
            secs = secs * 60 + int(float(part))
        return secs
    except ValueError:
        return 0  # unparseable text counts as "unknown"


def load_feed(source):
    """source can be a feed URL or a feed XML file saved on disk."""
    # If `source` is an existing local file, read it; otherwise download it as a URL.
    if os.path.exists(source):
        root = ET.parse(source).getroot()
    else:
        with fetch(source) as r:
            root = ET.parse(r).getroot()

    # The show's name lives at <channel><title>. Fall back to "Podcast" if it's missing.
    show = (root.findtext("channel/title") or "Podcast").strip()
    items = []
    for item in root.iter("item"):   # every <item> in the feed is one episode
        enc = item.find("enclosure") # the <enclosure> tag holds the audio file's URL, size and type
        if enc is None or not enc.get("url"):
            continue  # an item with no audio file can't be downloaded
        # Skip trailers. Items with no episodeType are treated as normal ("full") episodes.
        if (item.findtext(ITUNES + "episodeType") or "full").lower() == "trailer":
            continue
        # Convert the publish date (e.g. "Mon, 01 Jan 2024 10:00:00 +0000") to a timestamp
        # so episodes can be sorted by date. If it's missing or malformed, use 0.
        try:
            ts = parsedate_to_datetime(item.findtext("pubDate")).timestamp()
        except Exception:
            ts = 0
        # One dict per episode, holding everything the rest of the script needs.
        items.append({
            "title": (item.findtext("title") or "untitled").strip(),
            "url": enc.get("url"),
            "type": (enc.get("type") or "").lower(),         # e.g. "audio/mpeg"
            "size": to_int(enc.get("length")) or 0,          # bytes; 0 = feed didn't say
            "secs": parse_duration(item.findtext(ITUNES + "duration")),
            "ep": to_int(item.findtext(ITUNES + "episode")), # the feed's own number, or None
            "ts": ts,
        })
    return show, items


def assign_numbers(items):
    """Use the feed's episode numbers if they look reliable, else number by date."""
    # `have` = the episode numbers the feed provides (skipping episodes with none).
    have = [it["ep"] for it in items if it["ep"] is not None]
    # Trust the feed's numbers only if ALL of these are true:
    #   1. there are episodes at all,
    #   2. at least 80% of them carry a number (a few unnumbered bonus items are fine),
    #   3. no number repeats (shows that restart numbering every season would repeat them).
    if items and len(have) >= 0.8 * len(items) and len(set(have)) == len(have):
        numbered = [it for it in items if it["ep"] is not None]  # drop unnumbered items
        for it in numbered:
            it["num"] = it["ep"]  # "num" is the number we use in filenames
        dropped = len(items) - len(numbered)
        note = "feed episode numbers" + (f" ({dropped} unnumbered item(s) skipped)" if dropped else "")
        return sorted(numbered, key=lambda it: it["num"]), note
    # Otherwise number the episodes 1, 2, 3... oldest first, by publish date.
    items = sorted(items, key=lambda it: it["ts"])
    for i, it in enumerate(items, 1):
        it["num"] = i
    return items, "publish-date order (feed has no reliable episode numbers)"


# ---------- files ----------
def free_space(path):
    """Free bytes on the drive holding `path`, even if the folder doesn't exist yet."""
    # A folder we haven't created yet can't be measured, so walk up to the closest
    # parent that exists (it lives on the same drive) and measure that instead.
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:  # reached the top (e.g. "/") with nothing found; stop looping
            break
        path = parent
    return shutil.disk_usage(path).free


def safe_name(text, limit=100):
    # Remove characters that aren't allowed in filenames on Windows/Mac/Linux (\ / : * ? " < > |),
    # then trim spaces and trailing dots (Windows dislikes names ending in ".").
    text = re.sub(r'[\\/:*?"<>|]', "", text).strip().rstrip(".")
    # Cap the length so paths don't get too long; fall back to "untitled" if nothing is left.
    return text[:limit].strip() or "untitled"


def extension(item):
    # Pick the file extension from the feed's declared type, or from the URL as a backup.
    # The URL is lowercased and has any "?query" part removed first.
    t, u = item["type"], item["url"].lower().split("?")[0]
    if "mp4" in t or "m4a" in t or u.endswith(".m4a"):
        return ".m4a"
    return ".mp3"  # the default for audio


def show_progress(done, total, start):
    """Redraw a one-line progress bar: [#####     ]  42.0%  13.4/32.0 MB  5.2 MB/s"""
    # speed = bytes so far / seconds elapsed, shown in MB/s. max(..., 0.001) avoids dividing by zero.
    speed = done / max(time.time() - start, 0.001) / 1e6
    if total:
        frac = min(done / total, 1)    # fraction complete, never above 100%
        bar = "#" * int(frac * 30)     # the bar is 30 characters wide
        line = (f"   [{bar:<30}] {frac * 100:5.1f}%  "
                f"{done / 1e6:6.1f}/{total / 1e6:.1f} MB  {speed:4.1f} MB/s")
    else:  # server didn't say how big the file is
        line = f"   {done / 1e6:6.1f} MB  {speed:4.1f} MB/s"
    # "\r" returns the cursor to the start of the line so the next draw overwrites this one.
    sys.stdout.write("\r" + line)
    sys.stdout.flush()


def download(url, path, retries=3):
    # Download to "<name>.part" first, and rename it to the real name only when it finishes.
    # That way an interrupted download never looks like a finished episode.
    tmp = path + ".part"
    for attempt in range(1, retries + 1):
        try:
            # Open the connection and the output file together; both close automatically.
            with fetch(url, timeout=60) as resp, open(tmp, "wb") as f:
                total = to_int(resp.headers.get("Content-Length")) or 0  # size in bytes, 0 if unknown
                done = 0
                start = last = time.time()  # `last` = when we last redrew the progress bar
                while True:
                    chunk = resp.read(65536)  # read 64 KB at a time instead of loading the whole file
                    if not chunk:             # an empty read means the download is complete
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if time.time() - last >= 0.2:  # redraw ~5 times a second
                        show_progress(done, total, start)
                        last = time.time()
                show_progress(done, total, start)  # final draw so the bar ends at 100%
                sys.stdout.write("\n")             # move off the progress line
            os.replace(tmp, path)  # atomically rename .part to the final filename
            return True
        except Exception as e:
            sys.stdout.write("\n")
            print(f"   attempt {attempt}/{retries} failed: {e}")
            time.sleep(2 * attempt)  # wait a little longer after each failure (2s, 4s, 6s)
    return False  # all attempts failed


# ---------- main ----------
def main():
    # --- 1. Define the command-line options ---
    ap = argparse.ArgumentParser(description="Download podcasts from RSS feeds.")
    # nargs="+" lets you type search words without quotes: `malicious life`
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
    # Enforce the rule that downloading needs an output folder. Searching and dry runs
    # save nothing, so they're exempt. ap.error prints the message and exits with code 2.
    if not args.out and not (args.search or args.dry_run):
        ap.error("an output folder is required for downloading: add -o FOLDER "
                 "(e.g. -o ~/Music/Podcasts)")

    # --- 2. Work out which feed to use ---
    text = " ".join(args.source)
    if text.startswith(("http://", "https://")) or os.path.exists(text):
        feed = text  # it's already a feed URL or a saved feed file
    else:
        # Otherwise treat it as search words and ask Apple's directory.
        results = search_podcasts(text)
        if not results:
            sys.exit("No podcasts found for that search.")
        print_results(results)
        if args.search:
            return  # --search only lists matches, then stops
        choice = input("\nPick a number (Enter = 1, q = quit): ").strip().lower()
        if choice == "q":
            return
        idx = (to_int(choice) or 1) - 1  # blank or invalid text becomes 1; subtract 1 for a list index
        if not 0 <= idx < len(results):
            sys.exit("Invalid choice.")
        feed = results[idx]["feedUrl"]
        print(f"Using feed: {feed}\n")

    # --- 3. Load the episodes, number them, and apply --range / --latest ---
    show, items = load_feed(feed)
    items, how = assign_numbers(items)
    if args.range:
        items = [it for it in items if args.range[0] <= it["num"] <= args.range[1]]
    if args.latest:
        items = items[-args.latest:]  # items are sorted oldest-first, so the last N are the newest

    # --- 4. Work out where files go and total up the selection ---
    name = safe_name(args.name or show)  # --name overrides the feed's own title
    folder = None  # only set when -o was given (always the case when downloading)
    if args.out:
        # expanduser turns "~" into your home folder; abspath makes the path absolute.
        out = os.path.abspath(os.path.expanduser(args.out))
        folder = out if args.flat else os.path.join(out, name)  # --flat skips the show subfolder
    known = [it["size"] for it in items if it["size"] > 0]  # episodes whose size the feed reported
    total = sum(known)
    hours = sum(it["secs"] for it in items) / 3600
    unknown = len(items) - len(known)  # episodes with no reported size

    # --- 5. Print the summary ---
    print(f"Show: {name}")
    print(f"Saving to: {folder or '(not set - add -o FOLDER to download)'}")
    print(f"Episodes selected: {len(items)} (numbered by {how})")
    print(f"Total size: about {total / 1e9:.2f} GB"
          + (f" (+{unknown} episode(s) with unknown size)" if unknown else ""))
    if hours:
        print(f"Listening time: about {hours:.0f} hours")
    free = free_space(folder or ".")  # measure the destination drive (or the current one for a dry run)
    print(f"Free disk space: {free / 1e9:.1f} GB")
    if total > free:
        print("WARNING: not enough free space for all of this.")

    # --- 6. Stop here for dry runs (or if nothing was selected) ---
    if args.dry_run or not items:
        if args.dry_run:
            print("\nDry run, nothing downloaded.")
        return

    # --- 7. Download ---
    try:
        os.makedirs(folder, exist_ok=True)  # create the folder (and parents); fine if it already exists
    except OSError as e:
        sys.exit(f"Can't create the download folder '{folder}': {e}")
    existing = os.listdir(folder)  # what's already there, used to skip finished episodes
    for it in items:
        # Files are named "<Show> 001 - <title>.mp3". The "<Show> 001 - " part is how we
        # recognize an episode we already have, even if its title text changed.
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
