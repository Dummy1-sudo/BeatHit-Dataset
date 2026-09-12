"""Semantic row checks shared by build and verification entry points."""
import re

GAME_TAGS = {'video game music', 'video game soundtrack', 'game soundtrack', 'vgm',
             'game music', 'computer game music', 'video game score', 'game score',
             'original game soundtrack', 'game ost'}
BAD_GAME_WORK = re.compile(r'\b(?:soundtrack|original score|volume|vol\.?|music collection|for piano|played by)\b', re.I)
BAD_GAME_MEDIA = re.compile(r'\b(?:motion picture|original film|film soundtrack|movie soundtrack|television soundtrack|anime soundtrack|piano cover|tribute|lullaby|music box|karaoke|cover album|played by|remix album)\b', re.I)


def video_game_row_error(row, extra):
    work = str(row.get('screen_work') or '').strip()
    kind = extra.get('game_association_kind')
    if not work or extra.get('culture_category') != 'video_game_music':
        return 'missing game classification'
    if kind not in {'official_soundtrack_album', 'franchise_artist', 'explicit_track_reference', 'genre_album', 'listenbrainz_release_group_tag'}:
        return 'unknown association kind'
    if BAD_GAME_WORK.search(work):
        return 'soundtrack packaging left in game title'
    if BAD_GAME_MEDIA.search(f"{row.get('album') or ''} | {work} | {row.get('genres') or ''}"):
        return 'non-game or arrangement evidence'
    if kind == 'genre_album' and not extra.get('game_wikidata_id'):
        return 'genre-only album lacks independently identified game'
    if kind == 'listenbrainz_release_group_tag' and not (
        row.get('musicbrainz_recording_mbid') and extra.get('listenbrainz_source_scope') == 'release-group'
        and GAME_TAGS.intersection(extra.get('listenbrainz_source_tags') or [])
        and extra.get('explicit_soundtrack_release')):
        return 'missing recording/release-group game evidence'
    return None
