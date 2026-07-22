from music_megalist.countries import (
    CountryMarket,
    collapse_country_rows_for_megalist,
    parse_country_markets_html,
    parse_country_totals_html,
)


def test_country_market_discovery_excludes_global_and_recovers_names():
    html = '''<html><body>
    Global <a href="country/global_daily.html">Daily</a> (<a href="country/global_daily_totals.html">Totals</a>)<br>
    United States <a href="country/us_daily.html">Daily</a> (<a href="country/us_daily_totals.html">Totals</a>)<br>
    Japan <a href="country/jp_daily.html">Daily</a> (<a href="country/jp_daily_totals.html">Totals</a>)<br>
    </body></html>'''
    markets = parse_country_markets_html(html)
    assert [(m.code, m.name) for m in markets] == [('jp', 'Japan'), ('us', 'United States')]
    assert markets[0].totals_url.endswith('/spotify/country/jp_daily_totals.html')


def test_country_totals_parser_uses_chart_streams_and_spotify_id():
    html = '''<html><body>
    <div>Covers charts from 2017/01/01 to 2026/07/19.</div>
    <table>
      <tr><th>Artist and Title</th><th>Days</th><th>T10</th><th>Pk</th><th>PkStreams</th><th>Total</th></tr>
      <tr><td><a href="/spotify/artist/a.html">Artist A</a> - <a href="/spotify/track/ABC123.html">Song A</a></td><td>100</td><td>10</td><td>1(x2)</td><td>500,000</td><td>12,000,000</td></tr>
      <tr><td><a href="/spotify/artist/b.html">Artist B</a> - <a href="/spotify/track/XYZ789.html">Song B</a></td><td>50</td><td>2</td><td>7</td><td>200,000</td><td>5,000,000</td></tr>
    </table></body></html>'''
    market = CountryMarket('xx', 'Exampleland', 'https://kworb.net/spotify/country/xx_daily_totals.html')
    rows, meta = parse_country_totals_html(html, market=market, limit=2, retrieved_at='2026-07-20')
    assert [r.title for r in rows] == ['Song A', 'Song B']
    assert rows[0].spotify_track_id == 'ABC123'
    assert rows[0].metric_name == 'spotify_country_chart_streams'
    assert rows[0].listen_count == 12_000_000
    assert rows[0].extra['country_code'] == 'XX'
    assert rows[0].extra['peak_rank'] == 1
    assert rows[0].extra['peak_occurrences'] == 2
    assert meta['coverage_start'] == '2017-01-01'
    assert meta['coverage_end'] == '2026-07-19'
    assert meta['complete'] is True


def test_country_rows_collapse_for_megalist_sums_distinct_markets_only():
    html_us = '''<table><tr><td><a href="/spotify/artist/a.html">Artist</a> - <a href="/spotify/track/AAA.html">Hit</a></td><td>10</td><td>2</td><td>1</td><td>1000</td><td>10000</td></tr></table>'''
    html_jp = '''<table><tr><td><a href="/spotify/artist/a.html">Artist</a> - <a href="/spotify/track/AAA.html">Hit</a></td><td>8</td><td>1</td><td>2</td><td>900</td><td>7000</td></tr></table>'''
    us, _ = parse_country_totals_html(html_us, market=CountryMarket('us','United States','https://example/us'), limit=1)
    jp, _ = parse_country_totals_html(html_jp, market=CountryMarket('jp','Japan','https://example/jp'), limit=1)
    collapsed = collapse_country_rows_for_megalist(us + jp)
    assert len(collapsed) == 1
    row = collapsed[0]
    assert row.metric_name == 'spotify_regional_chart_streams_sum'
    assert row.listen_count == 17_000
    assert row.extra['country_chart_market_count'] == 2
    assert {x['country_code'] for x in row.extra['country_chart_appearances']} == {'US','JP'}


def test_country_totals_source_exhaustion_is_recorded_without_padding():
    html = """<table>
      <tr><td><a href='/spotify/artist/a.html'>A</a> - <a href='/spotify/track/A1.html'>One</a></td><td>10</td><td>2</td><td>1</td><td>1000</td><td>10000</td></tr>
      <tr><td><a href='/spotify/artist/b.html'>B</a> - <a href='/spotify/track/B1.html'>Two</a></td><td>8</td><td>1</td><td>2</td><td>900</td><td>7000</td></tr>
    </table>"""
    rows, meta = parse_country_totals_html(
        html, market=CountryMarket('xx','Exampleland','https://example/xx'), limit=1000
    )
    assert len(rows) == 2
    assert meta['available_unique_songs'] == 2
    assert meta['expected_rows'] == 2
    assert meta['source_exhausted_below_target'] is True
    assert meta['complete'] is False


def test_country_market_name_mapping_fixes_code_only_label():
    html = "AD <a href='country/ad_daily.html'>Daily</a><br>"
    markets = parse_country_markets_html(html)
    assert [(m.code,m.name) for m in markets] == [('ad','Andorra')]
