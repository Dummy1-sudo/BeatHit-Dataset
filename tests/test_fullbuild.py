from pathlib import Path
import pandas as pd
from music_megalist.fullbuild import _catalog_row_to_song, _parse_genres, _parse_theme_string, _screen_work_from_album


def test_explicit_count_fields_are_not_scores():
    r=pd.Series({
        'title':'Song','main_artist':'Artist','artists':'Artist','album_name':'Album',
        'release_date':'2024-01-01','release_year':2024,'genres':"['pop']",'streams':123456,
        'popularity':80,'track_score':None,'youtube_views':None,'track_id':'abc','source_url':'https://example.test'
    })
    row=_catalog_row_to_song(r)
    assert row.metric_name=='spotify_streams'
    assert row.listen_count==123456
    assert row.listen_source=='Spotify'
    assert row.view_count is None


def test_genre_and_theme_parsing():
    assert _parse_genres("['classical', 'baroque']") == ['baroque','classical']
    assert _parse_theme_string('1: "Connect" by ClariS (eps 1-12)') == ('Connect','ClariS')


def test_screen_work_cleanup():
    assert _screen_work_from_album('Example Movie (Original Motion Picture Soundtrack)') == 'Example Movie'


def test_missing_track_ids_do_not_collapse_catalog_keys(tmp_path):
    from music_megalist.fullbuild import _read_chunks, _identity
    p=tmp_path/'songs.csv'
    p.write_text('Artist Name,Song Name,Total Streams\nA,One,100\nB,Two,200\n',encoding='utf-8')
    df=next(_read_chunks(p,'test',chunksize=10))
    assert df['track_id'].tolist()==['','']
    keys=[_identity(t,a,tid) for t,a,tid in zip(df.title,df.main_artist,df.track_id)]
    assert len(set(keys))==2


def test_catalog_row_ignores_nan_track_id():
    import math
    r=pd.Series({
        'title':'Song','main_artist':'Artist','artists':'Artist','album_name':'',
        'release_date':'2024-01-01','release_year':2024,'genres':'','streams':100,
        'daily_streams':None,'popularity':None,'track_score':None,'youtube_views':None,
        'track_id':float('nan'),'isrc':None,'source_url':'https://example.test'
    })
    row=_catalog_row_to_song(r)
    assert row.spotify_track_id is None


def test_chart_history_total_streams_is_not_marked_cumulative(tmp_path):
    from music_megalist.fullbuild import _read_chunks
    p=tmp_path/'chart.csv'
    p.write_text('Artist Name,Song Name,Total Streams\nA,Song,99000000\n',encoding='utf-8')
    df=next(_read_chunks(p,'spotify_top10000_2023',chunksize=10))
    r=df.iloc[0]
    assert r['streams_metric_name']=='spotify_chart_streams_snapshot'
    assert bool(r['streams_is_cumulative']) is False


def test_specialist_direct_count_gets_comparable_fallback_score_not_unit_dominance():
    from music_megalist.fullbuild import _sort_and_rank
    from music_megalist.models import SongRow
    catalog=SongRow(title='Catalog',main_artist='A',metric_name='spotify_popularity',metric_value=90,metric_unit='score',overall_popularity_score=60,source_url='https://example.test/a')
    billion=SongRow(title='Billion',main_artist='B',metric_name='spotify_streams',metric_value=1_000_000_000,metric_unit='streams',listen_count=1_000_000_000,source_url='https://example.test/b')
    ranked=_sort_and_rank([billion,catalog])
    assert ranked[0].title=='Catalog'  # 60/100 cross-source composite > stream-only ~27/100 evidence score
    assert [r.rank for r in ranked]==[1,2]


def test_crossplatform_stream_snapshot_is_not_trusted_for_hard_threshold(tmp_path):
    from music_megalist.fullbuild import _read_chunks
    p=tmp_path/'cross.csv'
    p.write_text('Track,Artist,Spotify Streams\nSong,A,999999999\n',encoding='utf-8')
    df=next(_read_chunks(p,'spotify_2024_crossplatform',chunksize=10))
    r=df.iloc[0]
    assert r['streams_metric_name']=='spotify_streams_snapshot'
    assert bool(r['streams_is_cumulative']) is True
    assert pd.isna(r['trusted_cumulative_streams'])


def test_catalog_retains_trusted_stream_channel_across_higher_untrusted_snapshot(tmp_path, monkeypatch):
    import music_megalist.fullbuild as fb
    z=tmp_path/'zenodo.csv'
    x=tmp_path/'cross.csv'
    z.write_text('track_id,name,track_artists,streams\nz1,Song,A,20000000\n',encoding='utf-8')
    x.write_text('Track,Artist,Spotify Streams\nSong,A,99000000\n',encoding='utf-8')
    monkeypatch.setattr(fb,'ROOT',tmp_path)
    monkeypatch.setattr(fb,'CACHE',tmp_path/'cache')
    monkeypatch.setattr(fb,'REPORT',tmp_path/'BUILD_REPORT.json')
    status=fb.BuildStatus(started_at='test')
    out=fb.build_catalog({'spotify_zenodo_0_9m':z,'spotify_2024_crossplatform':x},status)
    df=fb.load_catalog(out)
    r=df.iloc[0]
    assert int(r['streams'])==99_000_000
    assert int(r['trusted_cumulative_streams'])==20_000_000
    assert 'zenodo' in str(r['trusted_streams_source_url'])


def test_selection_claim_rejects_same_spotify_id_with_different_text_labels():
    from music_megalist.fullbuild import _claim_selection
    used=set()
    a=pd.Series({'title':'Song A','main_artist':'Artist','track_id':'SAME123','isrc':None})
    b=pd.Series({'title':'Alternate Metadata Title','main_artist':'Different Credit','track_id':'SAME123','isrc':None})
    assert _claim_selection(a,used) is True
    assert _claim_selection(b,used) is False


def test_selection_claim_rejects_same_text_with_different_spotify_ids():
    from music_megalist.fullbuild import _claim_selection
    used=set()
    a=pd.Series({'title':'Same Song','main_artist':'Artist','track_id':'AAA','isrc':None})
    b=pd.Series({'title':'Same Song','main_artist':'Artist','track_id':'BBB','isrc':None})
    assert _claim_selection(a,used) is True
    assert _claim_selection(b,used) is False


def test_http_json_honors_429_retry_after(monkeypatch):
    import httpx
    import music_megalist.fullbuild as fb
    calls={'n':0}; sleeps=[]
    def handler(request):
        calls['n']+=1
        if calls['n']==1:
            return httpx.Response(429,headers={'Retry-After':'3'},request=request,json={'error':'rate'})
        return httpx.Response(200,request=request,json={'ok':True})
    monkeypatch.setattr(fb.time,'sleep',lambda seconds:sleeps.append(seconds))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fb._http_json(client,'GET','https://example.test') == {'ok':True}
    assert calls['n']==2
    assert sleeps==[3.0]
