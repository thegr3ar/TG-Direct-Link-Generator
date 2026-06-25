from __future__ import annotations
import emoji

# This file is a part of TG-Direct-Link-Generator
"""Robust title/year/TV extraction from inconsistent Telegram file names.

Channel files use many naming styles (plain, emoji-laden, country flags, dotted
release names, TV episode markers). This module normalises them into a clean
title plus structured metadata that can be searched against TMDB.
"""

import re
from dataclasses import dataclass
from typing import Optional


# Emoji, country-flag (regional indicators), symbols, arrows, dingbats, ZWJ.
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emoji & pictographs (+ supplemental/symbols)
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicator symbols (flags)
    "\U00002190-\U000021FF"  # arrows
    "\U00002B00-\U00002BFF"  # misc symbols and arrows
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U00002122\U00002139\U0000203C\U00002049"
    "\U0000200D"             # zero width joiner
    "\U000024C2"
    "]+",
    flags=re.UNICODE,
)

VIDEO_EXTENSIONS = (
    "mkv", "mp4", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg",
    "ts", "m2ts", "3gp", "ogv", "vob", "divx", "rmvb",
)
_EXT_RE = re.compile(r"\.(" + "|".join(VIDEO_EXTENSIONS) + r")$", re.IGNORECASE)

# Content inside brackets/braces is almost always release-group / junk metadata.
_BRACKET_RE = re.compile(r"[\[\{][^\]\}]*[\]\}]")

# TV episode markers.
_SXXEXX_RE = re.compile(r"\bS\s*(\d{1,2})\s*[\.\-_ ]?\s*E\s*(\d{1,3})\b", re.IGNORECASE)
_NxNN_RE = re.compile(r"\b(\d{1,2})\s*x\s*(\d{1,3})\b", re.IGNORECASE)
_SEASON_RE = re.compile(r"\bSeason\s*(\d{1,2})\b", re.IGNORECASE)
_EPISODE_RE = re.compile(r"\b(?:Episode|Ep)\s*\.?\s*(\d{1,3})\b", re.IGNORECASE)
# A bare "S01"/"S1" (season only, no episode) still implies a TV show.
_SEASON_ONLY_RE = re.compile(r"\bS\s*(\d{1,2})\b", re.IGNORECASE)
_TV_HINT_RE = re.compile(r"\b(series|tv\s*show|complete\s*series)\b", re.IGNORECASE)

# Parenthesised year is preferred; otherwise a standalone 1900-2099 token.
_YEAR_PAREN_RE = re.compile(r"[\(\[]\s*(19\d{2}|20\d{2})\s*[\)\]]")
_YEAR_BARE_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

# Runtime durations e.g. "2h 31m", "2h", "131min", "90 min".
_DURATION_RE = re.compile(r"\b\d{1,2}\s*h(?:\s*\d{1,2}\s*m(?:in)?)?\b", re.IGNORECASE)
_DURATION_MIN_RE = re.compile(r"\b\d{1,3}\s*min\b", re.IGNORECASE)

# Quality / codec / source / audio tags removed as whole words.
QUALITY_TOKENS = {
    "360p", "480p", "540p", "576p", "720p", "1080p", "1440p", "2160p", "4320p",
    "4k", "8k", "2k", "uhd", "hd", "fhd", "sd", "hdr", "hdr10", "sdr", "10bit",
    "8bit", "x264", "x265", "h264", "h265", "avc", "hevc", "xvid", "divx",
    "bluray", "blu-ray", "bdrip", "brrip", "webrip", "web-dl", "webdl", "web",
    "hdrip", "hdtv", "dvdrip", "dvdscr", "camrip", "cam", "hdcam", "remux",
    "proper", "repack", "extended", "uncut", "imax", "aac", "ac3", "eac3",
    "dts", "ddp", "dd5", "dd2", "mp3", "flac", "truehd", "atmos", "5.1", "7.1",
    "dual", "multi", "esub", "esubs", "msub", "msubs", "hc", "korsub",
}

# Channel-specific and provider / generic keywords (whole word, case-insensitive).
NOISE_KEYWORDS = {
    # Somali channel labels
    "magaca", "sanadka", "codenta", "astaan", "filim", "filimo", "aflaam",
    "aflaan", "somali",
    # Providers
    "netflix", "hulu", "amazon", "prime", "hbo", "disney", "hotstar", "zee5",
    "appletv", "peacock", "paramount",
    # Generic media words
    "film", "films", "movie", "movies", "fullmovie", "complete", "collection",
    "official", "trailer", "print", "rip", "encoded", "encode",
}

# Genre words (spec requires stripping e.g. Action/Drama from titles).
GENRE_KEYWORDS = {
    "action", "adventure", "animation", "comedy", "crime", "documentary",
    "drama", "family", "fantasy", "history", "horror", "music", "mystery",
    "romance", "romantic", "thriller", "war", "western", "scifi", "sci-fi",
    "fiction",
}

# Words that, alone, do not constitute a real title.
GENERIC_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "part", "vol", "volume", "untitled",
    "video", "new", "old", "best", "top", "hd",
}

# Default fallback year used by callers when TMDB has no date (kept here so the
# extractor stays the single source of naming/parsing truth).
_MIN_TITLE_LEN = 3


@dataclass
class ExtractResult:
    raw: str
    title: str
    year: Optional[int]
    is_tv_show: bool
    season: Optional[int]
    episode: Optional[int]
    valid: bool
    reason: str = ""

    @property
    def search_type(self) -> str:
        return "tv" if self.is_tv_show else "movie"


def remove_emojis(text: str) -> str:
    return emoji.replace_emoji(text, replace=" ")


def _strip_extension(text: str) -> str:
    return _EXT_RE.sub("", text)


def _extract_tv(text: str):
    """Return (cleaned_text, season, episode, is_tv) and strip TV markers."""
    season = episode = None
    is_tv = False

    m = _SXXEXX_RE.search(text)
    if m:
        season, episode = int(m.group(1)), int(m.group(2))
        is_tv = True
        text = _SXXEXX_RE.sub(" ", text)

    if not is_tv:
        m = _NxNN_RE.search(text)
        if m:
            season, episode = int(m.group(1)), int(m.group(2))
            is_tv = True
            text = _NxNN_RE.sub(" ", text)

    m = _SEASON_RE.search(text)
    if m:
        season = season or int(m.group(1))
        is_tv = True
        text = _SEASON_RE.sub(" ", text)

    m = _EPISODE_RE.search(text)
    if m:
        episode = episode or int(m.group(1))
        is_tv = True
        text = _EPISODE_RE.sub(" ", text)

    if not is_tv:
        m = _SEASON_ONLY_RE.search(text)
        if m:
            season = int(m.group(1))
            is_tv = True
            text = _SEASON_ONLY_RE.sub(" ", text)

    if not is_tv and _TV_HINT_RE.search(text):
        is_tv = True
    text = _TV_HINT_RE.sub(" ", text)

    return text, season, episode, is_tv


def _extract_year(text: str):
    """Return (cleaned_text, year). Prefer a parenthesised year."""
    m = _YEAR_PAREN_RE.search(text)
    if m:
        year = int(m.group(1))
        text = _YEAR_PAREN_RE.sub(" ", text, count=1)
        # Drop any other bare occurrences of the same year too.
        text = re.sub(r"\b" + str(year) + r"\b", " ", text)
        return text, year

    m = _YEAR_BARE_RE.search(text)
    if m:
        year = int(m.group(1))
        text = _YEAR_BARE_RE.sub(" ", text, count=1)
        return text, year

    return text, None


def _drop_tokens(text: str) -> str:
    """Remove quality/codec, genre and noise tokens (whole words)."""
    drop = QUALITY_TOKENS | NOISE_KEYWORDS | GENRE_KEYWORDS
    out = []
    for tok in re.split(r"\s+", text):
        bare = tok.strip(".,;:!?-_'\"()").lower()
        if not bare:
            continue
        if bare in drop:
            continue
        out.append(tok)
    return " ".join(out)


def _final_clean(text: str) -> str:
    text = re.sub(r"[\(\)\[\]\{\}]", " ", text)
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s*[-/:|+]\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" -:/.|,")


def _is_valid(title: str) -> tuple[bool, str]:
    cleaned = title.strip()
    if len(cleaned) < _MIN_TITLE_LEN:
        return False, "title too short"
    words = [w for w in re.split(r"\s+", cleaned.lower()) if w]
    if not words:
        return False, "empty title"
    if all(w in GENERIC_STOPWORDS for w in words):
        return False, "only generic words"
    return True, ""


def extract(filename: Optional[str]) -> ExtractResult:
    """Parse a raw file name into structured title metadata."""
    raw = filename or ""
    text = _strip_extension(raw)
    text = remove_emojis(text)
    text = _BRACKET_RE.sub(" ", text)
    text = text.replace(".", " ").replace("_", " ")

    text, season, episode, is_tv = _extract_tv(text)
    text, year = _extract_year(text)

    text = _DURATION_RE.sub(" ", text)
    text = _DURATION_MIN_RE.sub(" ", text)
    # Normalise common separators so joined tokens ("Action/Drama") tokenise.
    text = re.sub(r"[\/|,]+", " ", text)
    text = _drop_tokens(text)

    title = _final_clean(text)
    valid, reason = _is_valid(title)

    return ExtractResult(
        raw=raw,
        title=title,
        year=year,
        is_tv_show=is_tv,
        season=season,
        episode=episode,
        valid=valid,
        reason=reason,
    )
