import importlib.util
import os
import sys


def _load(mod_name, rel_path):
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", rel_path))
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


tmdb = _load("tmdb", os.path.join("main", "utils", "tmdb.py"))
database = _load("database", os.path.join("main", "utils", "database.py"))


def test_map_genres_from_names():
    assert tmdb.map_genres_to_categories(["Action", "Drama"]) == ["Action", "Drama"]


def test_map_genres_from_ids():
    assert tmdb.map_genres_to_categories([28, 18, 878]) == [
        "Action",
        "Drama",
        "Science Fiction",
    ]


def test_map_genres_dedupes():
    # TV "Action & Adventure" (10759) maps to Action; dedupe with explicit 28.
    assert tmdb.map_genres_to_categories([28, 10759]) == ["Action"]


def test_build_metadata_movie():
    details = {
        "id": 19995,
        "title": "Avatar",
        "release_date": "2009-12-18",
        "original_language": "en",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/back.jpg",
        "vote_average": 7.6,
        "overview": "A paraplegic Marine...",
        "genres": [{"id": 28, "name": "Action"}, {"id": 12, "name": "Adventure"}],
        "credits": {
            "cast": [
                {"name": "Sam Worthington", "character": "Jake Sully"},
                {"name": "Zoe Saldana", "character": "Neytiri"},
            ],
            "crew": [{"job": "Director", "name": "James Cameron"}],
        },
    }
    meta = tmdb.TMDBClient._build_metadata("movie", details)
    assert meta.tmdb_id == 19995
    assert meta.title == "Avatar"
    assert meta.year == 2009
    assert meta.language == "en"
    assert meta.poster_url == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert meta.rating == 7.6
    assert meta.director == "James Cameron"
    assert meta.is_tv_show is False
    assert len(meta.cast) == 2
    assert meta.cast[0]["name"] == "Sam Worthington"


def test_build_metadata_tv_uses_name_and_first_air_date():
    details = {
        "id": 1402,
        "name": "The Walking Dead",
        "first_air_date": "2010-10-31",
        "original_language": "en",
        "poster_path": None,
        "vote_average": 8.1,
        "genres": [{"id": 18, "name": "Drama"}],
        "created_by": [{"name": "Frank Darabont"}],
        "credits": {"cast": [], "crew": []},
    }
    meta = tmdb.TMDBClient._build_metadata("tv", details)
    assert meta.title == "The Walking Dead"
    assert meta.year == 2010
    assert meta.is_tv_show is True
    assert meta.poster_url is None
    assert meta.director == "Frank Darabont"


def test_slugify():
    assert database.slugify("Science Fiction") == "science-fiction"
    assert database.slugify("Action!!!") == "action"
    assert database.slugify("") == "unknown"
