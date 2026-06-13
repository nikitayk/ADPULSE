"""Unit tests for ``data_source`` — the IPinYou log streaming layer.

These tests depend only on the standard library (``data_source`` has no
third-party imports), so they are fast and run in any environment.
"""
import data_source as ds


# --------------------------------------------------------------------------- #
# Cell normalisation helpers
# --------------------------------------------------------------------------- #
class TestNormalise:
    def test_nv_strips_and_passes_through(self):
        assert ds._nv("  abc  ") == "abc"

    def test_nv_treats_null_like_tokens_as_none(self):
        for token in ("", "null", "Null", "NULL", "na", "NA", "   "):
            assert ds._nv(token) is None

    def test_nv_handles_none(self):
        assert ds._nv(None) is None

    def test_to_int_parses_numeric_strings(self):
        assert ds._to_int("42") == 42
        assert ds._to_int("42.9") == 42  # truncates via float()

    def test_to_int_uses_default_on_garbage(self):
        assert ds._to_int("abc", default=-1) == -1
        assert ds._to_int("null", default=7) == 7
        assert ds._to_int(None) is None


# --------------------------------------------------------------------------- #
# Row parsing / layout auto-detection
# --------------------------------------------------------------------------- #
def _row_24col():
    # BidID,Timestamp,Logtype,VisitorID,UA,IP,Region,City,Adexchange,Domain,URL,
    # AnonURLID,AdslotID,W,H,Visibility,Format,Floor,CreativeID,Biddingprice,
    # Payingprice,KeypageURL,AdvertiserID,UserProfileIDs
    return [
        "bid-1", "20130606000104407", "1", "vis-1", "Mozilla/5.0", "1.2.3.4",
        "1", "2", "3", "domain", "url", "anon", "slot-1", "300", "250",
        "2", "Fixed", "5", "creative", "100", "55", "kpurl", "1458", "tags",
    ]


def _row_20col():
    # BidID,Timestamp,VisitorID,UA,IP,Region,City,Adexchange,Domain,URL,
    # AnonURLID,AdslotID,W,H,Visibility,Format,Floor,CreativeID,AdvertiserID,Tags
    return [
        "bid-2", "20130606000104407", "vis-2", "Mozilla/5.0", "1.2.3.4",
        "1", "2", "3", "domain", "url", "anon", "slot-2", "300", "250",
        "2", "Fixed", "5", "creative", "3476", "tags",
    ]


class TestParseRow:
    def test_parses_24col_imp_layout(self):
        params, meta = ds._parse_row(_row_24col())
        assert params["bidId"] == "bid-1"
        assert params["advertiserId"] == 1458
        assert params["adSlotWidth"] == 300
        assert params["adSlotFloorPrice"] == 5
        # 24-col layout carries the real market price
        assert meta["paying_price"] == 55

    def test_parses_20col_bid_layout(self):
        params, meta = ds._parse_row(_row_20col())
        assert params["bidId"] == "bid-2"
        assert params["advertiserId"] == 3476
        # 20-col bid log has no paying price
        assert meta["paying_price"] is None

    def test_short_rows_are_rejected(self):
        assert ds._parse_row(["only", "three", "cols"]) == (None, None)

    def test_row_without_advertiser_is_rejected(self):
        row = _row_24col()
        row[22] = "null"  # AdvertiserID
        assert ds._parse_row(row) == (None, None)

    def test_null_tokens_become_defaults(self):
        row = _row_24col()
        row[17] = "null"  # Adslotfloorprice -> default 0
        params, _ = ds._parse_row(row)
        assert params["adSlotFloorPrice"] == 0


# --------------------------------------------------------------------------- #
# File-backed helpers and the DataSource itself
# --------------------------------------------------------------------------- #
def _write_tsv(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write("\t".join(str(c) for c in row) + "\n")


class TestLoadBidIdSet:
    def test_loads_first_column(self, tmp_path):
        p = tmp_path / "clk.06.txt"
        _write_tsv(p, [["bid-1", "x"], ["bid-2", "y"]])
        ids = ds._load_bidid_set(str(p))
        assert ids == {"bid-1", "bid-2"}

    def test_missing_file_yields_empty_set(self, tmp_path):
        assert ds._load_bidid_set(str(tmp_path / "nope.txt")) == set()

    def test_respects_limit(self, tmp_path):
        p = tmp_path / "clk.06.txt"
        _write_tsv(p, [["a"], ["b"], ["c"]])
        assert ds._load_bidid_set(str(p), limit=2) == {"a", "b"}


class TestDataSource:
    def test_available_when_bid_file_present(self, tmp_path):
        _write_tsv(tmp_path / "bid.06.txt", [_row_20col()])
        source = ds.DataSource(str(tmp_path), ["06"])
        assert source.available is True
        assert source.kind == "bid"

    def test_real_outcomes_join_by_bidid(self, tmp_path):
        _write_tsv(tmp_path / "imp.06.txt", [_row_24col()])
        _write_tsv(tmp_path / "clk.06.txt", [["bid-1"]])
        source = ds.DataSource(str(tmp_path), ["06"])
        assert source.kind == "imp"
        assert source.has_real_outcomes is True

        params, meta = next(source.stream())
        assert params["bidId"] == "bid-1"
        assert meta["clicked"] is True
        assert meta["converted"] is False

    def test_stream_labels_none_without_outcome_files(self, tmp_path):
        _write_tsv(tmp_path / "bid.06.txt", [_row_20col()])
        source = ds.DataSource(str(tmp_path), ["06"])
        _, meta = next(source.stream())
        assert meta["clicked"] is None

    def test_info_summary_shape(self, tmp_path):
        _write_tsv(tmp_path / "imp.06.txt", [_row_24col()])
        info = ds.DataSource(str(tmp_path), ["06"]).info()
        assert info["mode"] == "real"
        assert info["kind"] == "imp"
        assert info["files"] == ["imp.06.txt"]


class TestBuildDataSource:
    def test_returns_none_when_dir_missing(self, tmp_path):
        assert ds.build_data_source(str(tmp_path / "absent"), ["06"]) is None

    def test_returns_none_when_no_log_files(self, tmp_path):
        assert ds.build_data_source(str(tmp_path), ["06"]) is None

    def test_returns_source_when_files_present(self, tmp_path):
        _write_tsv(tmp_path / "bid.06.txt", [_row_20col()])
        source = ds.build_data_source(str(tmp_path), ["06"])
        assert source is not None
        assert source.available is True
