"""Parse whatever the user pasted into something a provider can resolve.

IMPORTANT — legal/architectural boundary
----------------------------------------
A realestate.com.au or Domain URL is accepted purely as an *identifier*. We parse
the slug locally to recover the suburb/state/listing-id the user is referring to
and then ask a licensed data provider about that property. Nothing in this
codebase fetches or scrapes those sites' HTML.

Recognised realestate.com.au shapes:
    /property-apartment-nsw-strathfield-145820394
    /property-house-nsw-concord+west-145820511
    /sold/property-house-nsw-burwood-144910222

Recognised domain.com.au shapes:
    /5-8-everton-road-burwood-nsw-2134-2019887654
    /property-profile/12-45-redmyre-road-strathfield-nsw-2135
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

AU_STATES = {"nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt"}

REA_HOSTS = {"realestate.com.au", "www.realestate.com.au", "m.realestate.com.au"}
DOMAIN_HOSTS = {"domain.com.au", "www.domain.com.au"}

_REA_SLUG = re.compile(
    r"property-(?P<ptype>[a-z\-]+?)-(?P<state>nsw|vic|qld|wa|sa|tas|act|nt)-(?P<suburb>[a-z0-9\+\-]+?)-(?P<listing_id>\d{6,})",
    re.IGNORECASE,
)
_DOMAIN_SLUG = re.compile(
    r"^(?P<body>[a-z0-9\-]+?)-(?P<state>nsw|vic|qld|wa|sa|tas|act|nt)-(?P<postcode>\d{4})(?:-(?P<listing_id>\d{6,}))?$",
    re.IGNORECASE,
)

# Street-type tokens mark the boundary between the street address and the suburb in a
# Domain slug ("5-8-everton-ROAD-burwood-nsw-2134"). Splitting on the *last* such token
# handles multi-word suburbs ("concord-west") and multi-word streets ("the-boulevarde").
_STREET_TYPE_TOKENS = {
    "street",
    "st",
    "road",
    "rd",
    "avenue",
    "ave",
    "drive",
    "dr",
    "place",
    "pl",
    "parade",
    "pde",
    "crescent",
    "cres",
    "way",
    "lane",
    "ln",
    "court",
    "ct",
    "terrace",
    "tce",
    "close",
    "cl",
    "boulevarde",
    "boulevard",
    "blvd",
    "grove",
    "gr",
    "circuit",
    "cct",
    "esplanade",
    "esp",
    "highway",
    "hwy",
    "square",
    "sq",
    "walk",
    "rise",
    "loop",
    "mews",
    "quay",
    "row",
}
_ADDRESS_HINT = re.compile(
    r"(?P<street_number>\d+[a-z]?(?:/\d+[a-z]?)?)\s+[\w'\.\- ]+?"
    r"(?:street|st|road|rd|avenue|ave|drive|dr|place|pl|parade|pde|crescent|cres|way|lane|ln|court|ct|terrace|tce|close|cl|boulevarde|boulevard|blvd|grove|gr|circuit|cct|esplanade|esp|highway|hwy)\b",
    re.IGNORECASE,
)
_POSTCODE = re.compile(r"\b(\d{4})\b")
_STATE_TOKEN = re.compile(r"\b(nsw|vic|qld|wa|sa|tas|act|nt)\b", re.IGNORECASE)

_TYPE_ALIASES = {
    "apartment": "apartment",
    "unit": "apartment",
    "studio": "apartment",
    "flat": "apartment",
    "house": "house",
    "acreage": "house",
    "semi": "house",
    "townhouse": "townhouse",
    "villa": "villa",
    "duplex": "duplex",
    "land": "land",
    "residential": "other",
}


class QueryKind(str, Enum):
    REA_URL = "realestate_com_au_url"
    DOMAIN_URL = "domain_com_au_url"
    UNSUPPORTED_URL = "unsupported_url"
    ADDRESS = "address"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedQuery:
    """Everything we could extract locally, before any provider call."""

    raw: str
    kind: QueryKind
    normalised_address: str | None = None
    suburb: str | None = None
    state: str | None = None
    postcode: str | None = None
    property_type: str | None = None
    listing_id: str | None = None
    host: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_resolvable(self) -> bool:
        return self.kind in {QueryKind.REA_URL, QueryKind.DOMAIN_URL, QueryKind.ADDRESS}

    def describe(self) -> str:
        if self.kind is QueryKind.REA_URL:
            return (
                f"realestate.com.au listing {self.listing_id} in {self.suburb or 'unknown suburb'}"
            )
        if self.kind is QueryKind.DOMAIN_URL:
            return f"domain.com.au listing for {self.normalised_address or 'an address'}"
        if self.kind is QueryKind.ADDRESS:
            return f"street address: {self.normalised_address}"
        if self.kind is QueryKind.UNSUPPORTED_URL:
            return f"unsupported URL host: {self.host}"
        return "unrecognised input"


def _titleise(slug: str) -> str:
    cleaned = slug.replace("+", " ").replace("-", " ").strip()
    return " ".join(part.capitalize() for part in cleaned.split() if part)


def _parse_rea(url: str, path: str) -> ParsedQuery:
    match = _REA_SLUG.search(path)
    if not match:
        return ParsedQuery(
            raw=url,
            kind=QueryKind.REA_URL,
            host="realestate.com.au",
            warnings=[
                "The realestate.com.au URL was recognised but its slug could not be parsed; "
                "no suburb or listing id could be extracted."
            ],
        )
    ptype_raw = match.group("ptype").split("-")[0].lower()
    return ParsedQuery(
        raw=url,
        kind=QueryKind.REA_URL,
        suburb=_titleise(match.group("suburb")),
        state=match.group("state").upper(),
        property_type=_TYPE_ALIASES.get(ptype_raw, "other"),
        listing_id=match.group("listing_id"),
        host="realestate.com.au",
    )


def _split_domain_body(body: str) -> tuple[str | None, str | None]:
    """Split "5-8-everton-road-burwood" into ("5-8 Everton Road", "Burwood")."""
    tokens = [token for token in body.split("-") if token]
    boundary = None
    for index, token in enumerate(tokens):
        # Never treat the final token as the street type; a suburb must remain.
        if token.lower() in _STREET_TYPE_TOKENS and index < len(tokens) - 1:
            boundary = index
    if boundary is None:
        return None, _titleise(body)
    street_tokens = tokens[: boundary + 1]
    suburb_tokens = tokens[boundary + 1 :]
    street = " ".join(part.capitalize() for part in street_tokens)
    # Re-join a leading unit/street number pair: "5 8 Everton Road" -> "5/8 Everton Road".
    if len(street_tokens) >= 2 and street_tokens[0].isdigit() and street_tokens[1].isdigit():
        street = f"{street_tokens[0]}/{street_tokens[1]} " + " ".join(
            part.capitalize() for part in street_tokens[2:]
        )
    return street.strip(), _titleise("-".join(suburb_tokens))


def _parse_domain(url: str, path: str) -> ParsedQuery:
    segment = path.rstrip("/").split("/")[-1]
    match = _DOMAIN_SLUG.match(segment)
    if not match:
        return ParsedQuery(
            raw=url,
            kind=QueryKind.DOMAIN_URL,
            host="domain.com.au",
            warnings=["The domain.com.au URL slug could not be parsed."],
        )
    street, suburb = _split_domain_body(match.group("body"))
    state = match.group("state").upper()
    postcode = match.group("postcode")
    address = ", ".join(
        part for part in (street, f"{suburb} {state} {postcode}" if suburb else None) if part
    )
    return ParsedQuery(
        raw=url,
        kind=QueryKind.DOMAIN_URL,
        normalised_address=address or None,
        suburb=suburb,
        state=state,
        postcode=postcode,
        listing_id=match.group("listing_id"),
        host="domain.com.au",
        warnings=[] if street else ["Could not isolate the street portion of the Domain slug."],
    )


def _parse_address(text: str) -> ParsedQuery:
    cleaned = " ".join(text.split())
    warnings: list[str] = []
    state_match = _STATE_TOKEN.search(cleaned)
    postcode_match = _POSTCODE.search(cleaned)
    has_street = bool(_ADDRESS_HINT.search(cleaned))

    if not has_street and not (state_match and postcode_match):
        return ParsedQuery(
            raw=text,
            kind=QueryKind.UNKNOWN,
            warnings=[
                "Input does not look like a supported property URL or an Australian "
                "street address (expected a street number and street type, e.g. "
                "'12/45 Redmyre Road, Strathfield NSW 2135')."
            ],
        )
    if not state_match:
        warnings.append("No Australian state token found; resolution may be ambiguous.")
    if not postcode_match:
        warnings.append("No postcode found; resolution may be ambiguous.")

    suburb = None
    if state_match:
        head = cleaned[: state_match.start()].rstrip(" ,")
        if "," in head:
            suburb = head.rsplit(",", 1)[-1].strip().title() or None
    return ParsedQuery(
        raw=text,
        kind=QueryKind.ADDRESS,
        normalised_address=cleaned,
        suburb=suburb,
        state=state_match.group(1).upper() if state_match else None,
        postcode=postcode_match.group(1) if postcode_match else None,
        warnings=warnings,
    )


def parse_query(raw: str) -> ParsedQuery:
    """Classify and normalise user input. Never raises on user input."""
    text = (raw or "").strip()
    if not text:
        return ParsedQuery(raw=raw or "", kind=QueryKind.UNKNOWN, warnings=["Empty input."])

    looks_like_url = text.lower().startswith(("http://", "https://")) or bool(
        re.match(r"^(www\.)?[a-z0-9\-]+\.(com\.au|com|au)/", text.lower())
    )
    if looks_like_url:
        candidate = text if "://" in text else f"https://{text}"
        parsed = urlparse(candidate)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        if host in REA_HOSTS:
            return _parse_rea(text, path)
        if host in DOMAIN_HOSTS:
            return _parse_domain(text, path)
        return ParsedQuery(
            raw=text,
            kind=QueryKind.UNSUPPORTED_URL,
            host=host or None,
            warnings=[
                f"'{host or text}' is not a supported property site. Supported: "
                "realestate.com.au, domain.com.au. You can also paste a street address."
            ],
        )
    return _parse_address(text)
