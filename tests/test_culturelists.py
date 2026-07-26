import json

import pandas as pd

from music_megalist.culturelists import (
    GAME_ARRANGEMENT_REJECT,
    GAME_MEDIA_REJECT,
    _game_title_from_explicit_soundtrack_release,
    _genre_matches,
    catalog_languages,
)
from music_megalist.io import read_rows, write_rows
from music_megalist.models import SongRow


def test_languages_are_lists_and_support_multiple_explicit_languages(tmp_path):
    row = pd.Series({"language": '["ja","en"]', "title": "Example", "genres": "pop"})
    assert catalog_languages(row) == ["ja", "en"]

    song = SongRow(
        title="Example",
        main_artist="Artist",
        languages=["ja", "en"],
        metric_name="youtube_views",
        metric_value=123,
        metric_unit="views",
        view_count=123,
        source_url="https://example.test",
    )
    path = tmp_path / "songs.csv"
    write_rows([song], path)
    raw = path.read_text(encoding="utf-8")
    assert '"[""ja"",""en""]"' in raw
    assert "\r\n" not in raw
    assert read_rows(path)[0].languages == ["ja", "en"]


def test_unknown_language_is_not_guessed_from_latin_title():
    row = pd.Series({"title": "This Looks English But Is Not Proven", "genres": "pop"})
    assert catalog_languages(row) == ["und"]


def test_instrumental_language_uses_zxx():
    row = pd.Series({"title": "Theme (Instrumental)", "genres": "soundtrack"})
    assert catalog_languages(row) == ["zxx"]


def test_genre_matching_is_category_based_not_short_substring_marker():
    assert _genre_matches(["k-pop"], ["k-pop"])
    assert _genre_matches(["avant-garde jazz"], ["jazz"])
    assert not _genre_matches(["canadian pop"], ["ia"])


def test_explicit_game_soundtrack_release_names_are_inferred_without_fuzzy_matching():
    assert (
        _game_title_from_explicit_soundtrack_release(
            "FINAL FANTASY X Original Soundtrack"
        )
        == "FINAL FANTASY X"
    )
    assert (
        _game_title_from_explicit_soundtrack_release(
            "ファイナルファンタジーX-2 オリジナル・サウンドトラック"
        )
        == "ファイナルファンタジーX-2"
    )
    assert _game_title_from_explicit_soundtrack_release("Oxenfree - OST") == "Oxenfree"
    assert _game_title_from_explicit_soundtrack_release("Unmarked Album") is None


def test_game_fallback_rejects_non_game_media_and_arrangements():
    assert GAME_MEDIA_REJECT.search("Example Original Motion Picture Soundtrack")
    assert GAME_ARRANGEMENT_REJECT.search("Game Music Piano Cover Collection")
