import csv
import json
from pathlib import Path

from music_megalist.validate import validate


def test_validate_ignores_coverage_report_schema(tmp_path: Path):
    data=tmp_path/'data'; data.mkdir()
    (data/'coverage_report.csv').write_text(
        'dataset,target,rows,complete,metric_coverage,notes\nworldwide,51000,51000,True,{},ok\n',
        encoding='utf-8',
    )
    assert validate(data)==[]


def test_country_shortfall_is_non_strict_but_strictly_incomplete(tmp_path: Path):
    data=tmp_path/'data'; countries=data/'countries'; countries.mkdir(parents=True)
    fn='xx_example_top1000.csv'
    fields=['rank','title','main_artist','metric_name','metric_value','metric_unit','listen_count','source_url','retrieved_at']
    with (countries/fn).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for i in range(1,3):
            w.writerow({'rank':i,'title':f'Song {i}','main_artist':'Artist','metric_name':'spotify_country_chart_streams','metric_value':100-i,'metric_unit':'streams','listen_count':100-i,'source_url':'https://example.test','retrieved_at':'2026-07-21'})
    index={
        'detected_country_markets':2,
        'successfully_built_markets':1,
        'complete_markets':0,
        'total_materialized_rows':2,
        'markets':[{'country_code':'XX','unique_songs':2,'file':fn}],
        'failures':[{'country_code':'YY','error':'404'}],
    }
    (countries/'index.json').write_text(json.dumps(index),encoding='utf-8')
    assert validate(data,require_complete=False)==[]
    strict=validate(data,require_complete=True)
    assert any('COUNTRY_COUNT XX: 2 != 1000' in e for e in strict)
    assert any('COUNTRY_MARKETS built 1 != detected 2' in e for e in strict)
    assert any('COUNTRY_FAILURES 1' in e for e in strict)
