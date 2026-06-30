"""SEC EDGAR puller parse layer (§5) — testable without network."""
from sec_puller import parse_hits

_SAMPLE = {
    "hits": {
        "hits": [
            {"_id": "0001140361-24-000123:q.htm",
             "_source": {"ciks": ["0001234567"],
                         "display_names": ["Acme Therapeutics Inc  (CIK 0001234567)"],
                         "file_date": "2024-05-01", "form": "10-Q"}},
        ]
    }
}
_SAMPLE_8K = {
    "hits": {
        "hits": [
            {"_id": "0000950170-24-000999:8k.htm",
             "_source": {"ciks": ["0007654321"],
                         "display_names": ["BigPharma Inc (CIK 0007654321)"],
                         "file_date": "2024-06-15", "form": "8-K"}},
        ]
    }
}


def test_parse_10q_filing():
    rows = parse_hits(_SAMPLE, "10-Q", "protocol_planning")
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "SEC EDGAR — 10-Q"
    assert r["event_type"] == "protocol_planning"
    assert "Acme Therapeutics" in r["sponsor_mentioned"]
    assert r["is_trial_announcement"] == 1
    # CIK-derived archive URL (leading zeros stripped).
    assert r["url"].startswith("https://www.sec.gov/Archives/edgar/data/1234567/")


def test_parse_8k_is_acquisition():
    rows = parse_hits(_SAMPLE_8K, "8-K", "acquisition")
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "SEC EDGAR — 8-K"
    assert r["event_type"] == "acquisition"
    assert r["is_trial_announcement"] == 0


def test_parse_empty():
    assert parse_hits({}, "10-Q", "protocol_planning") == []
    assert parse_hits({"hits": {"hits": []}}, "10-K", "protocol_planning") == []
