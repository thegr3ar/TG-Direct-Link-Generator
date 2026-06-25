import importlib.util
import os
import sys

# Load the extractor module directly by path so the test stays free of the
# Telegram/Mongo runtime dependencies pulled in by the `main` package __init__.
_MODULE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "main", "utils", "name_extractor.py")
)
_spec = importlib.util.spec_from_file_location("name_extractor", _MODULE_PATH)
name_extractor = importlib.util.module_from_spec(_spec)
sys.modules["name_extractor"] = name_extractor
_spec.loader.exec_module(name_extractor)
extract = name_extractor.extract


def test_simple_movie_with_year_and_quality():
    r = extract("Avatar.2009.1080p.mkv")
    assert r.valid
    assert r.title == "Avatar"
    assert r.year == 2009
    assert r.is_tv_show is False


def test_plain_movie_with_paren_year():
    r = extract("Legend (2014).mkv")
    assert r.valid
    assert r.title == "Legend"
    assert r.year == 2014
    assert r.is_tv_show is False


def test_movie_paren_year_with_quality():
    r = extract("The Dark Knight (2008) 1080p.mkv")
    assert r.valid
    assert r.title == "The Dark Knight"
    assert r.year == 2008


def test_emoji_and_channel_keywords():
    r = extract("💍MAGACA: Legend 💍SANADKA: (2014)")
    assert r.valid
    assert r.title == "Legend"
    assert r.year == 2014
    assert r.is_tv_show is False


def test_country_flag_and_emojis():
    r = extract("Filim 🇺🇸 2015 👉 Jurassic World 💯🔥👌")
    assert r.valid
    assert r.title == "Jurassic World"
    assert r.year == 2015


def test_invalid_no_title():
    r = extract("Netflix somali films Action/Drama 2h 31m")
    assert r.valid is False


def test_tv_show_sxxexx():
    r = extract("The.Walking.Dead.S01E01.1080p.mkv")
    assert r.valid
    assert r.title == "The Walking Dead"
    assert r.is_tv_show is True
    assert r.season == 1
    assert r.episode == 1
    assert r.search_type == "tv"


def test_tv_show_season_episode_words():
    r = extract("Breaking Bad - Season 1 - Episode 1.mkv")
    assert r.valid
    assert r.title == "Breaking Bad"
    assert r.is_tv_show is True
    assert r.season == 1
    assert r.episode == 1


def test_tv_show_nxnn_format():
    r = extract("Game of Thrones 1x05 720p.mkv")
    assert r.valid
    assert r.title == "Game of Thrones"
    assert r.is_tv_show is True
    assert r.season == 1
    assert r.episode == 5


def test_codec_and_group_stripped():
    r = extract("Inception.2010.1080p.BluRay.x264-[YTS].mp4")
    assert r.valid
    assert r.title == "Inception"
    assert r.year == 2010


def test_too_short_skipped():
    r = extract("AB.mkv")
    assert r.valid is False
    assert "short" in r.reason


def test_only_generic_words_skipped():
    r = extract("The Movie 1080p.mkv")
    assert r.valid is False


def test_empty_input():
    r = extract("")
    assert r.valid is False


def test_none_input():
    r = extract(None)
    assert r.valid is False


def test_dotted_title_without_year():
    r = extract("Mad.Max.Fury.Road.mkv")
    assert r.valid
    assert r.title == "Mad Max Fury Road"
    assert r.year is None


def test_year_preferred_from_parentheses():
    r = extract("1917 (2019) 1080p.mkv")
    assert r.valid
    assert r.year == 2019
    assert r.title == "1917"
