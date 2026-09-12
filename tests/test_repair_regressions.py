import csv
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from music_megalist import fullbuild as fb
from music_megalist import listenbrainz as lb
from music_megalist.hololive import credited_members, popular, select_joint
from music_megalist.io import read_rows, write_rows
from music_megalist.models import SongRow
from music_megalist.validate import _generic_csv_errors

MBID = '11111111-1111-1111-1111-111111111111'


def song(title='Song', artist='Artist', **kwargs):
    return SongRow(title=title, main_artist=artist, metric_name='youtube_views',
                   metric_value=kwargs.pop('metric_value',100), metric_unit='views',
                   view_count=kwargs.pop('view_count',100), source_url='https://example.test', **kwargs)


def client_factory(monkeypatch, module, handler):
    original = httpx.Client
    monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)


def test_tag_radio_hydrates_real_api_shape_and_migrates_existing_metadata(tmp_path, monkeypatch):
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.method=='GET':
            return httpx.Response(200,json=[{'recording_mbid':MBID,'source':'release-group','percent':0}])
        return httpx.Response(200,json={MBID:{'recording':{'name':'Actual song'},'artist':{'name':'Actual artist'},'tag':{'release_group':[{'tag':'jazz','count':1}]}}})
    client_factory(monkeypatch,lb,handler)
    status=SimpleNamespace(sources={},warnings=[])
    rows=lb.TagRadio(tmp_path,status,'test').recordings(['jazz'])
    mbid,meta,evidence=next(rows);rows.close()
    assert meta['recording']['name']=='Actual song' and evidence['percent']==0
    assert calls==['/1/lb-radio/tags','/1/metadata/recording/']
    calls.clear()
    rows=lb.TagRadio(tmp_path,status,'test').recordings(['jazz']);next(rows);rows.close()
    assert calls==[]
    # Existing video-game metadata is a reusable successful source checkpoint.
    other=tmp_path/'legacy';other.mkdir()
    (other/'listenbrainz_video_game_music.json').write_text(json.dumps({'metadata':{MBID:meta}}))
    rows=lb.TagRadio(other,status,'test').recordings(['jazz']);next(rows);rows.close()
    assert calls==['/1/lb-radio/tags']


def test_missing_recording_metadata_is_not_permanently_cached(tmp_path, monkeypatch):
    calls={'post':0}
    def handler(req):
        if req.method=='GET':
            return httpx.Response(200,json=[{'recording_mbid':MBID,'source':'recording','percent':10}])
        calls['post']+=1
        return httpx.Response(200,json={} if calls['post']==1 else {MBID:{'recording':{'name':'Recovered'},'artist':{'name':'Artist'},'tag':{'recording':[{'tag':'novelty','count':1}]}}})
    client_factory(monkeypatch,lb,handler)
    status=SimpleNamespace(sources={},warnings=[])
    assert list(lb.TagRadio(tmp_path,status,'test').recordings(['novelty']))==[]
    rows=lb.TagRadio(tmp_path,status,'test').recordings(['novelty'])
    assert next(rows)[1]['recording']['name']=='Recovered'
    rows.close();assert calls['post']==2


@pytest.mark.parametrize('code',[429,500,504])
def test_jikan_transient_errors_do_not_poison_theme_cache(monkeypatch,code):
    monkeypatch.setattr(fb.time,'sleep',lambda _:None)
    cache={};status=SimpleNamespace(warnings=[])
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(code,json={}))) as client:
        rows,ok,network=fb._jikan_theme_candidates(42,client,cache,status)
    assert not ok and network and '42' not in cache


def test_jikan_retries_legacy_empty_and_retains_successful_empty(monkeypatch):
    monkeypatch.setattr(fb.time,'sleep',lambda _:None)
    cache={'42':[]};status=SimpleNamespace(warnings=[]);calls=[]
    def handler(req):calls.append(req);return httpx.Response(200,json={'data':{'openings':[],'endings':[]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fb._jikan_theme_candidates(42,client,cache,status)==([],True,True)
        assert fb._jikan_theme_candidates(42,client,cache,status)==([],True,False)
    assert len(calls)==1


def test_anime_identity_uses_mal_ids_not_colliding_english_titles(tmp_path):
    path=tmp_path/'anime_songs.csv'
    rows=[song(rank=i,anime_title='Same English title',spotify_track_id='shared',extra={'mal_id':i}) for i in (1,2)]
    write_rows(rows,path)
    assert _generic_csv_errors(path)==[]
    rows[1].extra['mal_id']=1
    write_rows(rows,path)
    assert any('DUP_ANIME' in err for err in _generic_csv_errors(path))


def test_atomic_output_survives_serialization_error(tmp_path, monkeypatch):
    from music_megalist import io
    path=tmp_path/'songs.csv.gz';write_rows([song()],path);before=path.read_bytes()
    def fail(rows,path):path.write_bytes(b'truncated');raise OSError('disk failure')
    monkeypatch.setattr(io,'_write_rows',fail)
    with pytest.raises(OSError):write_rows([song('Replacement')],path)
    assert path.read_bytes()==before
    assert not list(tmp_path.glob('.beathit-*'))


def test_hololive_quota_combines_originals_and_covers_and_reports_absent_members():
    roster={'a':{'name':'A'},'absent':{'name':'Absent'}}
    originals=[song(f'Original {i}',extra={'hololive_member_ids':['a'],'hololive_youtube_views':i}) for i in range(3)]
    covers=[song(f'Cover {i}',extra={'hololive_member_ids':['a'],'hololive_youtube_views':i}) for i in range(2)]
    selected,report=select_joint({'original':originals,'cover':covers},roster,1000,target=2)
    assert sum(len(rows) for rows in selected.values())==5
    assert report['members']['a']['songs']==5
    assert report['members']['absent']['missing']==5
    assert not report['coverage_complete']


def test_hololive_high_reach_survives_nominal_cap_and_duplicate_uploads():
    rows=[song(f'Song {i}',extra={'hololive_member_ids':['a'],'hololive_youtube_views':2000}) for i in range(8)]
    rows.append(rows[0].model_copy(deep=True))
    selected,report=select_joint({'original':rows},{'a':{'name':'A'}},1000,target=3)
    assert len(selected['original'])==8
    assert report['members']['a']['songs']==8


@pytest.mark.parametrize('streams,views,threshold,expected',[
    (500001,0,None,True),(500000,9999999,1000,False),(None,1000,1000,False),
    (None,1001,1000,True),(None,9999999,None,False)])
def test_hololive_threshold_uses_trusted_spotify_then_youtube(streams,views,threshold,expected):
    row=song(extra={'hololive_trusted_spotify_streams':streams,'hololive_youtube_views':views})
    assert popular(row,threshold) is expected


def test_hololive_mentions_must_be_credited_and_shared_channels_handle_solos():
    roster={key:{'name':key,'aliases':[key],'channel_ids':[cid]} for key,cid in [('Member A','duo'),('Member B','duo'),('Guest C','guest')]}
    video={'title':'Song / Member A','channel':{'id':'duo'},'mentions':[{'id':'guest'}]}
    assert credited_members(video,roster)==['Member A']
    video['title']='Song / Member A feat. Guest C'
    assert credited_members(video,roster)==['Guest C','Member A']


def test_vocaloid_unavailable_is_distinct_from_failed_or_legacy_missing(tmp_path,monkeypatch):
    monkeypatch.setattr(fb,'CACHE',tmp_path/'cache');monkeypatch.setattr(fb,'ROOT',tmp_path)
    monkeypatch.setenv('YOUTUBE_API_KEY','test-key');monkeypatch.setattr(fb.time,'sleep',lambda _:None)
    fb.CACHE.mkdir();(fb.CACHE/'vocadb_youtube_view_counts.json').write_text(json.dumps({'videos':{'gone':{'views':None,'checked_at':int(fb.time.time())}}}))
    calls=[]
    def fetch(client,method,url,**kwargs):
        calls.append(kwargs['params']['id']);return {'items':[{'id':'ok','statistics':{'viewCount':'123'}},{'id':'nostats'}]}
    monkeypatch.setattr(fb,'_http_json',fetch)
    status=fb.BuildStatus(started_at='test')
    views,unresolved=fb._youtube_views_official(['gone','ok','nostats'],status)
    assert views=={'ok':123} and unresolved==1
    assert calls==['gone,ok,nostats']
    report=status.sources['vocaloid_youtube_view_counts']
    assert report['unavailable']==1 and not report['ok']
    cached=json.loads((fb.CACHE/'vocadb_youtube_view_counts.json').read_text())['videos']
    assert cached['gone']['availability']=='unavailable'
    assert cached['nostats']['availability']=='unresolved'


def test_game_packaging_cleanup_and_strong_source_evidence():
    from music_megalist.culturelists import _game_title_from_explicit_soundtrack_release
    from music_megalist.quality import video_game_row_error
    assert _game_title_from_explicit_soundtrack_release("Assassin's Creed 4: Black Flag (Sea Shanty Edition, Vol. 2) (Original Game Soundtrack)")=="Assassin's Creed 4: Black Flag"
    row={'screen_work':'Game','musicbrainz_recording_mbid':MBID}
    evidence={'culture_category':'video_game_music','game_association_kind':'listenbrainz_release_group_tag','listenbrainz_source_scope':'release-group','listenbrainz_source_tags':['chiptune'],'explicit_soundtrack_release':'Game OST'}
    assert video_game_row_error(row,evidence)
    evidence['listenbrainz_source_tags']=['video game music']
    assert video_game_row_error(row,evidence) is None


def test_checked_in_roster_has_real_channel_ids_and_no_navigation_channel():
    from music_megalist.hololive import load_roster
    roster=load_roster(Path(__file__).resolve().parents[1]/'data')
    assert len(roster)>=100
    assert all(r['channel_ids'] for r in roster.values())
    assert all('UCJFZiqLMntJufDCHc6bQixg' not in r['channel_ids'] for r in roster.values())
    assert roster['mori-calliope']['channel_ids']==['UCL_qhgtOy0dy1Agp8vkySQg']


def load_script(name):
    import sys
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    sys.path.insert(0,str(scripts))
    spec=importlib.util.spec_from_file_location(name,scripts/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_staging_includes_standard_outputs_and_excludes_raw_inputs(tmp_path,monkeypatch):
    import subprocess
    stage=load_script('stage_generated')
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    for rel in ['STATUS.json','data/screen_soundtracks/result.csv','data/countries/CA.csv',
                'data/worldwide/worldwide.csv','data/anime/anime_songs.partial.csv',
                'data/raw/private-source.csv','README.md']:
        p=tmp_path/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('test')
    monkeypatch.setattr(stage,'ROOT',tmp_path);stage.main()
    names=set(subprocess.check_output(['git','diff','--cached','--name-only'],cwd=tmp_path).decode().splitlines())
    assert names=={'STATUS.json','data/screen_soundtracks/result.csv','data/countries/CA.csv','data/worldwide/worldwide.csv'}


def test_restore_both_artifact_layouts_and_ignore_partial_files(tmp_path):
    restore=load_script('restore_outputs').restore
    for current in [True,False]:
        source=tmp_path/('current' if current else 'legacy')
        data=source/'data' if current else source
        (data/'anime').mkdir(parents=True)
        (data/'anime'/'anime_songs.csv').write_text('canonical')
        (data/'anime'/'anime_songs.partial.csv').write_text('unfinished')
        if current:(source/'STATUS.json').write_text('status')
        dest=tmp_path/('dest-current' if current else 'dest-legacy')
        restore(source,dest)
        assert (dest/'data/anime/anime_songs.csv').read_text()=='canonical'
        assert not (dest/'data/anime/anime_songs.partial.csv').exists()
        assert (dest/'STATUS.json').exists() is current


def test_target_checker_returns_failure_for_shortfalls(tmp_path,monkeypatch,capsys):
    checker=load_script('verify_targets')
    monkeypatch.setattr(checker,'ROOT',tmp_path);monkeypatch.setattr(checker,'DATA',tmp_path/'data')
    assert checker.main()==1
    report=json.loads(capsys.readouterr().out)
    assert not report['overall_complete']


def test_reuse_requires_corrected_builder_revision_but_reuses_unchanged_lists(tmp_path,monkeypatch):
    path=tmp_path/'anime_songs.csv';write_rows([song(rank=1,anime_title='Anime',extra={'mal_id':1})],path)
    monkeypatch.setattr(fb,'MATERIALIZED_OUTPUTS',{'anime':path})
    monkeypatch.setattr(fb,'FIXED_TARGETS',{'anime':1});monkeypatch.setattr(fb,'ROOT',tmp_path)
    monkeypatch.setattr(fb,'REPORT',tmp_path/'report.json')
    status=fb.BuildStatus(started_at='test',datasets={'anime':fb.DatasetStatus(target=1)})
    assert fb._reuse_complete_output('anime',status,{'anime':{'complete':True}}) is None
    old={'anime':{'complete':True,'builder_revision':fb.BUILDER_REVISIONS['anime']}}
    assert len(fb._reuse_complete_output('anime',status,old))==1


def test_successfully_cached_unavailable_vocaloid_video_does_not_block_completion(tmp_path,monkeypatch):
    monkeypatch.setattr(fb,'CACHE',tmp_path/'cache');monkeypatch.setattr(fb,'ROOT',tmp_path)
    monkeypatch.setenv('YOUTUBE_API_KEY','test-key')
    fb.CACHE.mkdir();(fb.CACHE/'vocadb_youtube_view_counts.json').write_text(json.dumps({'videos':{
        'gone':{'views':None,'checked_at':int(fb.time.time()),'availability':'unavailable'},
        'ok':{'views':123,'checked_at':int(fb.time.time()),'availability':'available'}}}))
    monkeypatch.setattr(fb,'_http_json',lambda *a,**k:pytest.fail('fresh cache should not refetch'))
    status=fb.BuildStatus(started_at='test')
    views,unresolved=fb._youtube_views_official(['gone','ok'],status)
    assert views=={'ok':123} and unresolved==0
    assert status.sources['vocaloid_youtube_view_counts']['ok']


def test_weak_jazz_tag_on_pop_rock_release_does_not_qualify():
    meta={'tag':{'release_group':[{'tag':'jazz','count':1,'genre_mbid':'jazz'},
                                  {'tag':'pop rock','count':5,'genre_mbid':'pop-rock'}]}}
    evidence={'source':'release-group','tag':'jazz','tag_count':14}
    assert lb.tag_support(meta,evidence) is None
    meta['tag']['release_group'][0]['count']=5
    assert lb.tag_support(meta,evidence)['matching_tag_votes']==5


def test_vtuber_builder_preserves_candidate_pool_for_joint_policy(tmp_path,monkeypatch):
    monkeypatch.setattr(fb,'DATA',tmp_path/'data');monkeypatch.setattr(fb,'CACHE',tmp_path/'cache')
    monkeypatch.setattr(fb,'REPORT',tmp_path/'report.json')
    monkeypatch.setattr(fb,'FIXED_TARGETS',{'vtuber_original':2})
    seed=fb.DATA/'seeds/hololive_members.json';seed.parent.mkdir(parents=True)
    seed.write_text(json.dumps({'members':[{'member_id':'member','name':'Member Name','aliases':['Member Name'],'channel_ids':['channel']}]}))
    videos=[{'id':str(i),'title':f'Song {i} - Member Name (Original Song)',
             'channel':{'id':'channel','english_name':'Member channel'},'mentions':[]} for i in range(4)]
    monkeypatch.setattr(fb,'_fetch_holodex_topic',lambda *a,**k:videos)
    monkeypatch.setattr(fb,'_youtube_views',lambda *a,**k:{str(i):100+i for i in range(4)})
    monkeypatch.setattr(fb,'_fetch_holostats_rows',lambda **k:[])
    status=fb.BuildStatus(started_at='test')
    result=fb.build_vtuber(pd.DataFrame(),status,original=True)
    assert len(result)==2
    pool=read_rows(fb.CACHE/'vtuber_original_candidates.jsonl.gz')
    assert len(pool)==4
    assert {row.title for row in pool}=={f'Song {i}' for i in range(4)}
    assert all(row.extra['hololive_member_ids']==['member'] for row in pool)


def test_vtuber_outage_preserves_existing_rows(tmp_path,monkeypatch):
    monkeypatch.setattr(fb,'DATA',tmp_path/'data');monkeypatch.setattr(fb,'CACHE',tmp_path/'cache')
    monkeypatch.setattr(fb,'REPORT',tmp_path/'report.json')
    monkeypatch.setattr(fb,'FIXED_TARGETS',{'vtuber_original':2})
    seed=fb.DATA/'seeds/hololive_members.json';seed.parent.mkdir(parents=True);seed.write_text('{"members":[]}')
    output=fb.DATA/'vtuber_original/vtuber_original_10000.csv'
    write_rows([song('Preserved one'),song('Preserved two')],output)
    monkeypatch.setattr(fb,'_fetch_holodex_topic',lambda *a,**k:[])
    monkeypatch.setattr(fb,'_fetch_holostats_rows',lambda **k:[])
    result=fb.build_vtuber(pd.DataFrame(),fb.BuildStatus(started_at='test'),original=True)
    assert {r.title for r in result}=={'Preserved one','Preserved two'}


def test_hololive_report_detects_missing_reserved_song(tmp_path):
    from music_megalist.hololive import coverage_errors,OVERRIDE_VIDEO_ID
    seed=tmp_path/'seeds/hololive_members.json';seed.parent.mkdir()
    seed.write_text(json.dumps({'members':[{'member_id':'a','name':'A'}]}))
    row=song('Present',extra={'hololive_member_ids':['a']})
    write_rows([row],tmp_path/'vtuber_original/vtuber_original_10000.csv')
    write_rows([],tmp_path/'vtuber_non_original/vtuber_non_original_10000.csv')
    report={'members':{'a':{'songs':1,'missing':4}},'required_song_keys':[['vtuber_original','missing','artist']],
            'youtube_threshold':{'video_id':OVERRIDE_VIDEO_ID,'observed_views':1450000,'rounded_down_to':100000,'threshold':1400000}}
    (tmp_path/'hololive_coverage.json').write_text(json.dumps(report))
    assert any('mandatory songs absent' in error for error in coverage_errors(tmp_path))


def test_explicit_cover_markers_do_not_reject_ordinary_song_titles():
    assert fb.VTUBER_COVER_MARKER.search('Some Song (Cover)')
    assert fb.VTUBER_COVER_MARKER.search('【歌ってみた】Song')
    assert not fb.VTUBER_COVER_MARKER.search('Cover Me In Sunshine')


def test_holodex_http_error_retains_cached_scan(tmp_path,monkeypatch):
    monkeypatch.setattr(fb,'CACHE',tmp_path)
    (tmp_path/'holodex_Original_Song.json').write_text(json.dumps({'videos':[{'id':'saved'}],'complete':False}))
    def fail(*a,**k):raise fb.SourceHTTPError(403,'HTTP 403')
    monkeypatch.setattr(fb,'_http_json',fail)
    status=fb.BuildStatus(started_at='test')
    assert fb._fetch_holodex_topic('Original_Song',status)==[{'id':'saved'}]
    assert not status.sources['holodex_Original_Song']['complete']


def test_youtube_permission_failure_stops_remaining_batches(tmp_path,monkeypatch):
    monkeypatch.setattr(fb,'CACHE',tmp_path/'cache');monkeypatch.setattr(fb,'ROOT',tmp_path)
    monkeypatch.setenv('YOUTUBE_API_KEY','test-key');calls=[]
    def fail(*a,**k):calls.append(1);raise fb.SourceHTTPError(403,'HTTP 403 key invalid')
    monkeypatch.setattr(fb,'_http_json',fail)
    status=fb.BuildStatus(started_at='test')
    views,unresolved=fb._youtube_views_official([str(i) for i in range(120)],status)
    assert len(calls)==1 and unresolved==120 and not views
    assert status.sources['vocaloid_youtube_view_counts']['live_requested']==50


def test_anime_uses_cached_jikan_themes_when_network_budget_is_zero(tmp_path,monkeypatch):
    monkeypatch.setattr(fb,'DATA',tmp_path/'data');monkeypatch.setattr(fb,'CACHE',tmp_path/'cache')
    monkeypatch.setattr(fb,'REPORT',tmp_path/'report.json');monkeypatch.setattr(fb,'FIXED_TARGETS',{'anime':1})
    monkeypatch.setenv('BEATHIT_JIKAN_MAX_QUERIES','0');monkeypatch.setenv('BEATHIT_JIKAN_MAX_SECONDS','0')
    anime={'id':1,'idMal':1,'title':{'english':'Anime'},'popularity':100}
    monkeypatch.setattr(fb,'fetch_anilist_top',lambda *a,**k:[anime])
    monkeypatch.setattr(fb,'_load_mal_anime_rank_fallback',lambda *a:[])
    monkeypatch.setattr(fb,'fetch_animethemes_all',lambda *a:({},{}))
    monkeypatch.setattr(fb,'_load_mal_theme_fallback',lambda *a:{})
    fb.CACHE.mkdir();(fb.CACHE/'jikan_themes.json').write_text(json.dumps({'1':{'checked_at':fb.time.time(),
        'themes':[{'title':'Cached song','artists':['Artist'],'type':'OP','sequence':1}]}}))
    status=fb.BuildStatus(started_at='test')
    rows=fb.build_anime(pd.DataFrame(),{},status)
    assert len(rows)==1 and rows[0].title=='Cached song'
    assert status.sources['jikan_theme_fallback']['queried']==0


def test_radio_outage_has_a_circuit_breaker(tmp_path,monkeypatch):
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(503,json={})
    client_factory(monkeypatch,lb,handler)
    status=SimpleNamespace(sources={},warnings=[])
    assert list(lb.TagRadio(tmp_path,status,'outage').recordings(['jazz','bebop','swing','bossa nova']))==[]
    assert len(calls)==9  # three requests, each with at most three attempts
    assert status.sources['listenbrainz_outage']['stop_reason']=='request_budget_or_circuit_breaker'


def test_vtuber_title_cleanup_does_not_duplicate_an_existing_video():
    from music_megalist.dedupe import dedupe
    old=song('Song - Artist (Official)',vtuber='Artist',extra={'holodex_video_id':'abcdefghijk'})
    new=song('Song',vtuber='Artist',extra={'holodex_video_id':'abcdefghijk','hololive_member_ids':['member']})
    rows=dedupe([old,new])
    assert len(rows)==1 and rows[0].extra['hololive_member_ids']==['member']
