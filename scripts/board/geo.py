"""Location parsing and user-configurable ranking.

Board sources emit free-text locations ('NYC', 'South SF', 'Remote in USA',
'Toronto, ON, Canada'). This module turns those into a tier plus a distance so
the board can rank two preferred states first, then remote roles, the rest of
the US by distance from the configured home point, and everything else.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .config import board_config

_CONFIG = board_config()
HOME_LAT = float(_CONFIG["home"]["latitude"])
HOME_LON = float(_CONFIG["home"]["longitude"])
HOME_LABEL = str(_CONFIG["home"]["label"])
PRIMARY_STATE = str(_CONFIG.get("primary_state", "")).upper()
SECONDARY_STATE = str(_CONFIG.get("secondary_state", "")).upper()

TIER_NJ = 0
TIER_NY = 1
TIER_REMOTE_US = 2
TIER_US = 3
TIER_INTL = 4
TIER_UNKNOWN = 5

TIER_LABELS = {
    TIER_NJ: str(_CONFIG.get("primary_label", "Primary area")),
    TIER_NY: str(_CONFIG.get("secondary_label", "Secondary area")),
    TIER_REMOTE_US: "Remote (US)",
    TIER_US: "Rest of US",
    TIER_INTL: "International",
    TIER_UNKNOWN: "Unspecified",
}

# Sources abbreviate heavily. Expand before parsing.
CITY_ALIASES = {
    "nyc": ("New York", "NY"),
    "new york city": ("New York", "NY"),
    "manhattan": ("New York", "NY"),
    "brooklyn": ("Brooklyn", "NY"),
    "sf": ("San Francisco", "CA"),
    "south sf": ("South San Francisco", "CA"),
    "sfo": ("San Francisco", "CA"),
    "la": ("Los Angeles", "CA"),
    "dc": ("Washington", "DC"),
    "washington dc": ("Washington", "DC"),
    "bay area": ("San Jose", "CA"),
    "silicon valley": ("San Jose", "CA"),
    "philly": ("Philadelphia", "PA"),
    "chi": ("Chicago", "IL"),
    "atl": ("Atlanta", "GA"),
    "boston area": ("Boston", "MA"),
}

STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR",
}

# Approximate population centroids, good enough for distance ranking.
STATE_COORDS = {
    "AL": (32.81, -86.79), "AK": (61.37, -152.40), "AZ": (33.73, -111.43),
    "AR": (34.97, -92.37), "CA": (36.12, -119.68), "CO": (39.06, -105.31),
    "CT": (41.60, -72.76), "DE": (39.32, -75.51), "DC": (38.90, -77.03),
    "FL": (27.77, -81.69), "GA": (33.04, -83.64), "HI": (21.09, -157.50),
    "ID": (44.24, -114.48), "IL": (40.35, -88.99), "IN": (39.85, -86.26),
    "IA": (42.01, -93.21), "KS": (38.53, -96.73), "KY": (37.67, -84.67),
    "LA": (31.17, -91.87), "ME": (44.69, -69.38), "MD": (39.06, -76.80),
    "MA": (42.23, -71.53), "MI": (43.33, -84.54), "MN": (45.69, -93.90),
    "MS": (32.74, -89.68), "MO": (38.46, -92.29), "MT": (46.92, -110.45),
    "NE": (41.13, -98.27), "NV": (38.31, -117.06), "NH": (43.45, -71.56),
    "NJ": (40.30, -74.52), "NM": (34.84, -106.25), "NY": (42.17, -74.95),
    "NC": (35.63, -79.81), "ND": (47.53, -99.78), "OH": (40.39, -82.76),
    "OK": (35.57, -96.93), "OR": (44.57, -122.07), "PA": (40.59, -77.21),
    "PR": (18.22, -66.59), "RI": (41.68, -71.51), "SC": (33.86, -80.95),
    "SD": (44.30, -99.44), "TN": (35.75, -86.69), "TX": (31.05, -97.56),
    "UT": (40.15, -111.86), "VT": (44.05, -72.71), "VA": (37.77, -78.17),
    "WA": (47.40, -121.49), "WV": (38.49, -80.95), "WI": (44.27, -89.62),
    "WY": (42.76, -107.30),
}

# Cities worth pinning precisely because they are common in job feeds.
CITY_COORDS = {
    ("new york", "NY"): (40.71, -74.01),
    ("new york city", "NY"): (40.71, -74.01),
    ("brooklyn", "NY"): (40.68, -73.94),
    ("queens", "NY"): (40.73, -73.79),
    # Frequently occurring regional cities.
    ("jersey city", "NJ"): (40.73, -74.08),
    ("newark", "NJ"): (40.74, -74.17),
    ("princeton", "NJ"): (40.35, -74.66),
    ("new brunswick", "NJ"): (40.49, -74.45),
    ("piscataway", "NJ"): (40.55, -74.46),
    ("iselin", "NJ"): (40.57, -74.32),
    ("woodbridge", "NJ"): (40.56, -74.28),
    ("rahway", "NJ"): (40.61, -74.28),
    ("union city", "NJ"): (40.78, -74.02),
    ("clifton", "NJ"): (40.86, -74.16),
    ("livingston", "NJ"): (40.80, -74.31),
    ("whippany", "NJ"): (40.82, -74.42),
    ("hanover", "NJ"): (40.82, -74.37),
    ("morris plains", "NJ"): (40.84, -74.48),
    ("parsippany", "NJ"): (40.86, -74.43),
    ("parsippany-troy hills", "NJ"): (40.86, -74.43),
    ("mahwah", "NJ"): (41.09, -74.14),
    ("secaucus", "NJ"): (40.79, -74.06),
    ("raritan", "NJ"): (40.57, -74.63),
    ("bridgewater", "NJ"): (40.59, -74.62),
    ("holmdel", "NJ"): (40.37, -74.17),
    ("eatontown", "NJ"): (40.30, -74.05),
    ("trenton", "NJ"): (40.22, -74.76),
    ("hamilton", "NJ"): (40.21, -74.68),
    ("hamilton township", "NJ"): (40.21, -74.68),
    ("hopewell township", "NJ"): (40.36, -74.82),
    ("camden", "NJ"): (39.93, -75.12),
    ("mount laurel", "NJ"): (39.93, -74.89),
    ("mt laurel township", "NJ"): (39.93, -74.89),
    ("mays landing", "NJ"): (39.45, -74.73),
    ("jackson township", "NJ"): (40.11, -74.34),
    ("delanco", "NJ"): (40.05, -74.95),
    ("morristown", "NJ"): (40.80, -74.48),
    ("bedminster", "NJ"): (40.67, -74.64),
    ("basking ridge", "NJ"): (40.71, -74.55),
    ("berkeley heights", "NJ"): (40.68, -74.44),
    ("summit", "NJ"): (40.72, -74.36),
    ("florham park", "NJ"): (40.79, -74.39),
    ("warren", "NJ"): (40.63, -74.52),
    ("somerset", "NJ"): (40.50, -74.49),
    ("edison", "NJ"): (40.52, -74.41),
    ("princeton junction", "NJ"): (40.32, -74.62),
    # NY towns beyond the city, several of them upstate.
    ("fishkill", "NY"): (41.54, -73.90),
    ("yorktown heights", "NY"): (41.27, -73.78),
    ("albany", "NY"): (42.65, -73.76),
    ("schenectady", "NY"): (42.81, -73.94),
    ("niskayuna", "NY"): (42.80, -73.85),
    ("latham", "NY"): (42.75, -73.76),
    ("rome", "NY"): (43.21, -75.46),
    ("east syracuse", "NY"): (43.07, -76.08),
    ("syracuse", "NY"): (43.05, -76.15),
    ("williamsville", "NY"): (42.96, -78.74),
    ("painted post", "NY"): (42.16, -77.09),
    ("bohemia", "NY"): (40.77, -73.12),
    ("armonk", "NY"): (41.13, -73.71),
    ("ithaca", "NY"): (42.44, -76.50),
    ("philadelphia", "PA"): (39.95, -75.17),
    ("stamford", "CT"): (41.05, -73.54),
    ("white plains", "NY"): (41.03, -73.76),
    ("rochester", "NY"): (43.16, -77.61),
    ("buffalo", "NY"): (42.89, -78.88),
    ("boston", "MA"): (42.36, -71.06),
    ("cambridge", "MA"): (42.37, -71.11),
    ("washington", "DC"): (38.90, -77.03),
    ("arlington", "VA"): (38.88, -77.10),
    ("pittsburgh", "PA"): (40.44, -79.996),
    ("baltimore", "MD"): (39.29, -76.61),
    ("san francisco", "CA"): (37.77, -122.42),
    ("south san francisco", "CA"): (37.65, -122.41),
    ("san jose", "CA"): (37.34, -121.89),
    ("palo alto", "CA"): (37.44, -122.14),
    ("mountain view", "CA"): (37.39, -122.08),
    ("santa clara", "CA"): (37.35, -121.96),
    ("sunnyvale", "CA"): (37.37, -122.04),
    ("los angeles", "CA"): (34.05, -118.24),
    ("san diego", "CA"): (32.72, -117.16),
    ("seattle", "WA"): (47.61, -122.33),
    ("bellevue", "WA"): (47.61, -122.20),
    ("redmond", "WA"): (47.67, -122.12),
    ("austin", "TX"): (30.27, -97.74),
    ("dallas", "TX"): (32.78, -96.80),
    ("houston", "TX"): (29.76, -95.37),
    ("chicago", "IL"): (41.88, -87.63),
    ("atlanta", "GA"): (33.75, -84.39),
    ("denver", "CO"): (39.74, -104.99),
    ("phoenix", "AZ"): (33.45, -112.07),
    ("charlotte", "NC"): (35.23, -80.84),
    ("raleigh", "NC"): (35.78, -78.64),
    ("miami", "FL"): (25.76, -80.19),
    ("detroit", "MI"): (42.33, -83.05),
    ("minneapolis", "MN"): (44.98, -93.27),
    ("ann arbor", "MI"): (42.28, -83.74),
}

REMOTE_RE = re.compile(r"\b(remote|work from home|wfh|virtual|anywhere)\b", re.I)
US_WORDS = {"usa", "us", "united states", "u.s.", "u.s.a.", "america", "nationwide"}

# Only used to classify a bare country string as international.
KNOWN_COUNTRIES = {
    "canada", "uk", "united kingdom", "england", "scotland", "ireland",
    "germany", "france", "spain", "italy", "netherlands", "sweden", "norway",
    "denmark", "finland", "poland", "switzerland", "austria", "belgium",
    "portugal", "israel", "india", "china", "japan", "korea", "south korea",
    "singapore", "australia", "new zealand", "brazil", "mexico", "argentina",
    "chile", "colombia", "taiwan", "hong kong", "vietnam", "thailand",
    "malaysia", "indonesia", "philippines", "uae", "united arab emirates",
    "saudi arabia", "egypt", "south africa", "nigeria", "kenya", "turkey",
    "czechia", "czech republic", "romania", "hungary", "greece", "ukraine",
    "pakistan", "bangladesh", "peru", "costa rica", "panama", "uruguay",
}

# Canadian/other subnational codes that collide with nothing in STATE_COORDS.
NON_US_REGION_CODES = {
    "ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU",
}


@dataclass(frozen=True)
class Place:
    """One parsed location."""

    raw: str
    city: str | None
    state: str | None
    country: str | None
    remote: bool
    tier: int
    distance_mi: float | None

    @property
    def tier_label(self) -> str:
        return TIER_LABELS[self.tier]

    def display(self) -> str:
        if self.remote and not self.city:
            return "Remote (US)" if self.tier == TIER_REMOTE_US else self.raw
        if self.city and self.state:
            return f"{self.city}, {self.state}"
        return self.raw


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles."""
    radius = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _coords_for(city: str | None, state: str | None) -> tuple[float, float] | None:
    if city and state:
        hit = CITY_COORDS.get((city.lower(), state))
        if hit:
            return hit
    if state:
        return STATE_COORDS.get(state)
    return None


def _normalise(raw: str) -> str:
    text = raw.strip().strip(",")
    text = re.sub(r"\s+", " ", text)
    # 'Remote in USA' / 'Hybrid - Austin, TX' style prefixes.
    text = re.sub(r"^(hybrid|onsite|on-site)\s*[-–:]\s*", "", text, flags=re.I)
    return text


def parse_location(raw: str) -> Place:
    """Parse one free-text location string into a ranked Place."""
    text = _normalise(raw or "")
    if not text:
        return Place(raw, None, None, None, False, TIER_UNKNOWN, None)

    remote = bool(REMOTE_RE.search(text))
    # Strip the remote marker so 'Remote in USA' / 'Remote - Austin, TX' parse.
    body = REMOTE_RE.sub(" ", text)
    body = re.sub(r"\b(in|at|from|only|-|–|,)\b", " ", body, flags=re.I)
    body = re.sub(r"\s+", " ", body).strip(" ,-–")

    parts = [p.strip() for p in re.split(r",", text) if p.strip()]
    city = state = country = None

    # Country-qualified: 'Toronto, ON, Canada' or 'London, UK'.
    if parts:
        tail = parts[-1].lower().strip(".")
        if tail in KNOWN_COUNTRIES:
            country = parts[-1]
            if len(parts) >= 2:
                city = parts[0]
            return Place(raw, city, None, country, remote, TIER_INTL, None)

    # Whole string is a country or US marker.
    flat = body.lower().strip(".")
    if flat in US_WORDS or (remote and not body):
        tier = TIER_REMOTE_US if remote else TIER_US
        return Place(raw, None, None, "United States", remote, tier, None)
    if flat in KNOWN_COUNTRIES:
        return Place(raw, None, None, body, remote, TIER_INTL, None)

    alias = CITY_ALIASES.get(flat)
    if alias:
        city, state = alias
    elif len(parts) >= 2:
        candidate = parts[1].strip().upper()
        if candidate in NON_US_REGION_CODES:
            return Place(raw, parts[0], None, "Canada", remote, TIER_INTL, None)
        if candidate in STATE_COORDS:
            city, state = parts[0], candidate
        else:
            named = STATE_NAME_TO_CODE.get(parts[1].strip().lower())
            if named:
                city, state = parts[0], named
    if not state:
        # Bare state name, or a bare city alias inside a longer string.
        named = STATE_NAME_TO_CODE.get(flat)
        if named:
            state = named
        else:
            alias = CITY_ALIASES.get(flat)
            if alias:
                city, state = alias

    if not state:
        tier = TIER_REMOTE_US if remote else TIER_UNKNOWN
        return Place(raw, city or (parts[0] if parts else None), None, None, remote, tier, None)

    coords = _coords_for(city, state)
    distance = haversine_mi(HOME_LAT, HOME_LON, *coords) if coords else None

    if PRIMARY_STATE and state == PRIMARY_STATE:
        tier = TIER_NJ
    elif SECONDARY_STATE and state == SECONDARY_STATE:
        tier = TIER_NY
    elif remote:
        tier = TIER_REMOTE_US
    else:
        tier = TIER_US
    return Place(raw, city, state, "United States", remote, tier, distance)


def best_place(locations: list[str]) -> Place:
    """Pick the most favourable location of a multi-location posting.

    A role listed in both Austin and NYC is an NYC role for ranking purposes.
    """
    if not locations:
        return Place("", None, None, None, False, TIER_UNKNOWN, None)
    places = [parse_location(loc) for loc in locations if loc]
    if not places:
        return Place("", None, None, None, False, TIER_UNKNOWN, None)
    return min(places, key=lambda p: (p.tier, p.distance_mi if p.distance_mi is not None else 9_999))


def sort_key(place: Place) -> tuple[int, float]:
    """Board ordering: preferred tier first, then distance from home."""
    return (place.tier, place.distance_mi if place.distance_mi is not None else 9_999)
