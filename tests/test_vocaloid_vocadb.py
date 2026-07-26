from music_megalist.fullbuild import (
    _normalize_existing_vocaloid_row,
    _vocadb_official_youtube_pvs,
    _vocadb_song_credit,
)
from music_megalist.models import SongRow


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


def _existing_vocaloid_row(pv_views):
    pvs = [
        {
            "video_id": f"video{i:06d}"[-11:],
            "views": views,
            "author": "Producer",
        }
        for i, views in enumerate(pv_views)
    ]
    return SongRow(
        title="Test Song",
        main_artist="Producer",
        featured_artists=["Hatsune Miku"],
        metric_name="youtube_views",
        metric_value=float(sum(pv_views)),
        metric_unit="views",
        view_count=sum(pv_views),
        source_url="https://www.youtube.com/watch?v=oldoldold00",
        extra={
            "vocadb_song_type": "Original",
            "youtube_pv_type": "Original",
            "youtube_pv_service": "Youtube",
            "voice_synth_vocalists": ["Hatsune Miku"],
            "official_youtube_pvs": pvs,
        },
    )


def test_vocaloid_does_not_qualify_by_summing_two_subthreshold_pvs():
    row = _existing_vocaloid_row([60_000_000, 50_000_000])
    assert _normalize_existing_vocaloid_row(row) is None


def test_vocaloid_qualifies_by_one_individual_official_pv():
    row = _existing_vocaloid_row([110_000_000, 50_000_000])
    normalized = _normalize_existing_vocaloid_row(row)
    assert normalized is not None
    assert normalized.view_count == 110_000_000
    assert normalized.metric_value == 110_000_000
    assert normalized.extra["qualification_method"] == "single_official_original_youtube_pv"
