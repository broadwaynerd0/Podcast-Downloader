# Podcast Downloader

A small Python script that downloads episodes from any **public podcast RSS feed**, and can search for feeds by name. One file, no dependencies.

## Features

- **Search for a show** by name and get its feed URL
- **Dry run** to see episode count, total size and listening time before downloading anything
- **Choose where files go** (required, so nothing lands somewhere unexpected)
- **Live progress bar** for each download
- **Safe to re-run:** finished episodes are skipped, partial downloads use a `.part` file and are retried
- **Smart numbering:** uses the feed's episode numbers when they're reliable, otherwise numbers by publish date
- Download everything, a range, or just the newest N episodes

## Requirements

- Python 3.8 or newer
- Nothing to install (standard library only)

## Usage

```bash
# Find a show's feed URL
python3 podcast_downloader.py "malicious life" --search

# Check size and episode count first (no download, no folder needed)
python3 podcast_downloader.py <feed-url> --dry-run

# Download everything into a folder you choose
python3 podcast_downloader.py <feed-url> -o ~/Music/Podcasts

# Only the newest 10 episodes
python3 podcast_downloader.py <feed-url> -o ~/Music/Podcasts --latest 10

# Episodes 1 through 50
python3 podcast_downloader.py <feed-url> -o ~/Music/Podcasts --range 1 50
```

If you pass search words instead of a URL (without `--search`), the script lists matches and asks you to pick one.

## Options

| Option | What it does |
|---|---|
| `-o`, `--out FOLDER` | **Required for downloading.** Folder to save into (`~` and full paths work) |
| `--flat` | Save files directly in the folder, with no show-name subfolder |
| `--dry-run` | Show episode count, total size, listening time and free disk space only |
| `--search` | List search results and feed URLs, then exit |
| `--range LO HI` | Only episodes LO to HI (inclusive) |
| `--latest N` | Only the newest N episodes |
| `--name NAME` | Override the show name used for the folder and filenames |

## Output layout

```
<folder>/
  <Show Name>/
    <Show Name> 001 - <Episode title>.mp3
    <Show Name> 002 - <Episode title>.mp3
```

## Responsible use

This tool is meant for personal, offline listening to podcasts whose publishers already offer their episodes freely through public RSS feeds. It does not bypass paywalls, logins or DRM.

- Respect each creator's terms, and don't redistribute episodes you download.
- The script pauses one second between downloads to be gentle on servers. Please don't remove that.
- Show search uses Apple's public iTunes Search API and is subject to Apple's terms.

## License

MIT. See [LICENSE](LICENSE).
