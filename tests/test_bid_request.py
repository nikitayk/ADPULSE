"""Sanity tests for the BidRequest data model (pure stdlib, no deps)."""
from BidRequest import BidRequest


def test_round_trips_all_fields():
    br = BidRequest()
    br.setBidId("abc")
    br.setTimestamp("20130606000104407")
    br.setAdvertiserId(3476)
    br.setAdSlotFloorPrice(50)
    br.setUserAgent("UA")

    assert br.getBidId() == "abc"
    assert br.getTimestamp() == "20130606000104407"
    assert br.getAdvertiserId() == 3476
    assert br.getAdSlotFloorPrice() == 50
    assert br.getUserAgent() == "UA"


def test_defaults_are_none():
    br = BidRequest()
    assert br.getBidId() is None
    assert br.getAdvertiserId() is None
    assert br.getUserAgent() is None
