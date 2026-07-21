from music_megalist.models import SongRow
from music_megalist.dedupe import dedupe

def row(title,artist,spotify=None):
    return SongRow(title=title,main_artist=artist,metric_name="test",metric_value=1,metric_unit="plays",source_url="https://example.com",spotify_track_id=spotify)

def test_spotify_exact_dedupe():
    out=dedupe([row("A","B","x"),row("A alt","B","x")])
    assert len(out)==1


def test_real_cumulative_count_beats_proxy_duplicate():
    count=SongRow(title='Same',main_artist='Artist',metric_name='spotify_streams',metric_value=10_000_000,metric_unit='streams',listen_count=10_000_000,source_url='https://example.com/count')
    proxy=SongRow(title='Same',main_artist='Artist',metric_name='anime_popularity_proxy',metric_value=99999999,metric_unit='anilist_users',overall_popularity_score=99,source_url='https://example.com/proxy')
    out=dedupe([proxy,count])
    assert len(out)==1
    assert out[0].metric_name=='spotify_streams'
    assert out[0].listen_count==10_000_000
