"""URL and address parsing — the front door, and the place bad input arrives."""

from __future__ import annotations

import pytest

from app.ingest.url_parser import QueryKind, parse_query

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "suburb", "state", "listing_id", "property_type"),
    [
        (
            "https://www.realestate.com.au/property-apartment-nsw-strathfield-145820394",
            "Strathfield",
            "NSW",
            "145820394",
            "apartment",
        ),
        (
            "https://www.realestate.com.au/sold/property-house-nsw-concord+west-144910222",
            "Concord West",
            "NSW",
            "144910222",
            "house",
        ),
        (
            "https://www.realestate.com.au/property-unit-vic-south+yarra-987654321",
            "South Yarra",
            "VIC",
            "987654321",
            "apartment",
        ),
    ],
)
def test_parses_realestate_urls(url, suburb, state, listing_id, property_type):
    parsed = parse_query(url)
    assert parsed.kind is QueryKind.REA_URL
    assert parsed.suburb == suburb
    assert parsed.state == state
    assert parsed.listing_id == listing_id
    assert parsed.property_type == property_type


@pytest.mark.parametrize(
    ("url", "address", "suburb", "postcode"),
    [
        (
            "https://www.domain.com.au/5-8-everton-road-burwood-nsw-2134-2019887654",
            "5/8 Everton Road, Burwood NSW 2134",
            "Burwood",
            "2134",
        ),
        (
            "https://www.domain.com.au/24-the-boulevarde-concord-west-nsw-2138-2011234567",
            "24 The Boulevarde, Concord West NSW 2138",
            "Concord West",
            "2138",
        ),
    ],
)
def test_parses_domain_urls_with_multiword_suburbs(url, address, suburb, postcode):
    parsed = parse_query(url)
    assert parsed.kind is QueryKind.DOMAIN_URL
    assert parsed.normalised_address == address
    assert parsed.suburb == suburb
    assert parsed.postcode == postcode


def test_parses_plain_address():
    parsed = parse_query("12/45 Redmyre Road, Strathfield NSW 2135")
    assert parsed.kind is QueryKind.ADDRESS
    assert parsed.state == "NSW"
    assert parsed.postcode == "2135"
    assert parsed.is_resolvable


def test_rejects_unsupported_property_site_without_crashing():
    parsed = parse_query("https://www.zillow.com/homedetails/12345")
    assert parsed.kind is QueryKind.UNSUPPORTED_URL
    assert not parsed.is_resolvable
    assert "not a supported property site" in parsed.warnings[0]


@pytest.mark.parametrize("value", ["", "   ", "banana", "hello world", "?????"])
def test_garbage_input_is_unknown_and_never_raises(value):
    parsed = parse_query(value)
    assert parsed.kind is QueryKind.UNKNOWN
    assert not parsed.is_resolvable
    assert parsed.warnings


def test_address_without_state_still_parses_but_warns():
    parsed = parse_query("45 Redmyre Road, Strathfield")
    assert parsed.kind is QueryKind.ADDRESS
    assert any("state" in warning.lower() for warning in parsed.warnings)
