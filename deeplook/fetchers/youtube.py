import asyncio
import concurrent.futures
import json
import re
from datetime import datetime, timedelta

import httpx
import yt_dlp

from ..debug_log import log


# Keywords that make a transcript segment worth keeping
TRANSCRIPT_KEYWORDS = [
    "revenue", "guidance", "margin", "growth",
    "risk", "outlook", "forecast", "tvl", "protocol",
]

# Titles that qualify a video for transcript download (Layer 2)
TRANSCRIPT_TITLE_TRIGGERS = [
    "earnings call", "quarterly results", "investor day",
]


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _parse_json3_to_segments(data: str) -> list[str]:
    """Parse json3 subtitle format into text segments."""
    try:
        obj = json.loads(data)
        segs = []
        for event in obj.get("events", []):
            parts = [s.get("utf8", "").strip() for s in event.get("segs", [])]
            text = " ".join(p for p in parts if p and p != "\n")
            if text:
                segs.append(text)
        return segs
    except Exception:
        return []


def _parse_vtt_to_segments(data: str) -> list[str]:
    """Parse VTT subtitle format into text segments."""
    segs = []
    seen_last = None
    for line in data.split("\n"):
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and line != seen_last:
            segs.append(line)
            seen_last = line
    return segs


def _fetch_sub_url(url: str, timeout: int = 10) -> str:
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.text
    except Exception:
        pass
    return ""


def _extract_relevant_segments(entry: dict, transcript_timeout: int = 10) -> str:
    """Download transcript and return only keyword-relevant segments (≤15k chars)."""
    sub_sources = [
        entry.get("subtitles", {}).get("en", []),
        entry.get("automatic_captions", {}).get("en", []),
    ]
    for lang_subs in entry.get("automatic_captions", {}).values():
        sub_sources.append(lang_subs)

    for formats in sub_sources:
        if not formats:
            continue
        sorted_fmts = sorted(
            formats,
            key=lambda f: {"json3": 0, "vtt": 1, "srv3": 2}.get(f.get("ext", ""), 9),
        )
        for fmt in sorted_fmts:
            url = fmt.get("url", "")
            ext = fmt.get("ext", "")
            if not url or ext not in ("json3", "vtt", "srv3"):
                continue
            raw = _fetch_sub_url(url, timeout=transcript_timeout)
            if not raw:
                continue

            if ext == "json3":
                segments = _parse_json3_to_segments(raw)
            else:
                segments = _parse_vtt_to_segments(raw)

            if not segments:
                continue

            # Filter to only relevant segments
            relevant = [
                seg for seg in segments
                if any(kw in seg.lower() for kw in TRANSCRIPT_KEYWORDS)
            ]
            log("youtube", "TRANSCRIPT_FILTER",
                f"total={len(segments)} relevant={len(relevant)}")

            if relevant:
                text = " ".join(relevant)
            else:
                # No keyword hits — fall back to full transcript (truncated)
                text = " ".join(segments)

            return text[:15000]

    return "[No transcript available]"


def _is_video_too_old(upload_date: str, max_age_days: int) -> bool:
    """Check if a video's upload_date (YYYYMMDD) exceeds max_age_days."""
    if not upload_date:
        return False
    try:
        pub = datetime.strptime(upload_date, "%Y%m%d")
        return pub < datetime.now() - timedelta(days=max_age_days)
    except ValueError:
        return False


def _qualifies_for_transcript(title: str, channel: str) -> bool:
    """Layer 2 gate: should we download the transcript for this video?"""
    title_lower = title.lower()
    return any(trigger in title_lower for trigger in TRANSCRIPT_TITLE_TRIGGERS)


# ─────────────────────────────────────────────
# Core fetch (blocking, runs in thread)
# ─────────────────────────────────────────────

def _fetch_youtube(query: str, max_results: int = 5,
                   max_age_days: int | None = None,
                   transcript_timeout: int = 10,
                   max_transcript_videos: int = 2) -> dict:
    """
    Two-layer YouTube fetch:
      Layer 1 — metadata only (title, channel, date, views, description)
                 filter: max_age_days, view_count > 1000
      Layer 2 — targeted transcript for earnings calls / investor day videos only
                 keeps only keyword-relevant segments
    """
    log("youtube", "START", query)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 10,
        "ignoreerrors": True,
        "format": "bestaudio/best",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    except Exception as e:
        log("youtube", "FAIL", f"extract_info error: {e}")
        return {"source": "youtube", "videos": [], "success": False, "error": str(e)}

    videos = []

    for entry in (info.get("entries", []) if info else []):
        if not entry:
            continue
        try:
            video_id = entry.get("id", "")
            title = entry.get("title", "")
            channel = entry.get("channel", entry.get("uploader", ""))
            upload_date = entry.get("upload_date", "")
            view_count = entry.get("view_count") or 0
            description = (entry.get("description") or "")[:500]
            url = entry.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}")

            # ── Layer 1 filters ───────────────────────────────────────────
            if max_age_days and _is_video_too_old(upload_date, max_age_days):
                log("youtube", "SKIP_OLD", f"{title!r} ({upload_date})")
                print(f"[strategy] youtube SKIP (too old): {title!r} ({upload_date})")
                continue

            if view_count < 1000:
                log("youtube", "SKIP_VIEWS", f"{title!r} views={view_count}")
                print(f"[strategy] youtube SKIP (low views={view_count}): {title!r}")
                continue

            # ── Transcript via youtube-transcript-api ─────────────────────
            def _get_transcript(vid_id: str) -> str | None:
                from youtube_transcript_api import YouTubeTranscriptApi
                fetched = YouTubeTranscriptApi().fetch(vid_id, languages=["en"])
                full_text = " ".join(s.text for s in fetched)
                return full_text[:3000]

            transcript = None
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
                    _future = _ex.submit(_get_transcript, video_id)
                    transcript = _future.result(timeout=transcript_timeout)
                log("youtube", "TRANSCRIPT_DONE", f"{title!r} len={len(transcript or '')}")
            except Exception as _e:
                log("youtube", "TRANSCRIPT_SKIP", f"{title!r} reason={_e}")

            videos.append({
                "source": "youtube",
                "url": url,
                "title": title,
                "channel": channel,
                "upload_date": upload_date,
                "view_count": view_count,
                "description": description,
                "transcript": transcript,
                "summary": "",
            })
            log("youtube", "VIDEO", f"title={title!r}")

        except Exception as e:
            log("youtube", "SKIP", f"video_id={entry.get('id', '?')} error={e}")

    if not videos and max_age_days:
        print(f"[strategy] youtube: all results filtered (age/views) — returning empty")

    log("youtube", "SUCCESS", f"videos={len(videos)}")
    return {"source": "youtube", "videos": videos, "success": True}


async def fetch_youtube(company_name: str, query: str | None = None,
                        max_results: int = 5, max_age_days: int | None = None,
                        transcript_timeout: int = 10) -> dict:
    resolved_query = query or f"{company_name} CEO founder interview"
    print(f"[strategy] youtube query: {resolved_query!r} max_age_days={max_age_days}")
    try:
        return await asyncio.to_thread(
            _fetch_youtube,
            resolved_query,
            max_results,
            max_age_days,
            transcript_timeout,
        )
    except Exception as e:
        log("youtube", "FAIL", str(e))
        return {"source": "youtube", "videos": [], "success": False, "error": str(e)}
