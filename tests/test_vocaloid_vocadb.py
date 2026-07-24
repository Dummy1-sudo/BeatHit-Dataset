from music_megalist.fullbuild import (
    _vocadb_official_youtube_pvs,
    _vocadb_song_credit,
)


def test_vocadb_pv_filter_accepts_only_enabled_original_youtube():
    item = {
        "pvs": [
            {"service": "Youtube", "pvType": "Original", "pvId": "abcdefghijk", "disabled": False},
            {"service": "Youtube", "pvType": "Reprint", "pvId": "bbbbbbbbbbb", "disabled": False},
            {"service": "Youtube", "pvType": "Other", "pvId": "ccccccccccc", "disabled": False},
            {"service": "NicoNicoDouga", "pvType": "Original", "pvId": "sm9", "disabled": False},
            {"service": "Youtube", "pvType": "Original", "pvId": "ddddddddddd", "disabled": True},
        ]
    }
    assert [pv["video_id"] for pv in _vocadb_official_youtube_pvs(item)] == ["abcdefghijk"]


def test_vocadb_song_credit_prefers_producer_and_features_voicebank():
    item = {
        "artists": [
            {
                "artist": {"id": 1, "name": "Producer P", "artistType": "Producer"},
                "effectiveRoles": "Composer",
                "isSupport": False,
            },
            {
                "artist": {"id": 2, "name": "Hatsune Miku", "artistType": "Vocaloid"},
                "effectiveRoles": "Vocalist",
                "isSupport": False,
            },
        ]
    }
    main, featured, metadata = _vocadb_song_credit(item)
    assert main == "Producer P"
    assert featured == ["Hatsune Miku"]
    assert metadata["producer_artists"] == ["Producer P"]
    assert metadata["voice_synth_vocalists"] == ["Hatsune Miku"]


def test_human_vocalist_is_not_misclassified_as_voice_synth():
    item = {
        "artists": [
            {
                "artist": {"id": 1, "name": "Producer P", "artistType": "Producer"},
                "effectiveRoles": "Composer",
                "isSupport": False,
            },
            {
                "artist": {"id": 2, "name": "Human Singer", "artistType": "Person"},
                "effectiveRoles": "Vocalist",
                "isSupport": False,
            },
        ]
    }
    main, featured, metadata = _vocadb_song_credit(item)
    assert main == "Producer P"
    assert metadata["voice_synth_vocalists"] == []
    assert "Human Singer" not in featured
