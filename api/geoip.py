"""
Local, offline IPv4 -> country lookup for the analytics dashboard.

No visitor IP is ever sent anywhere for this: geoip_data/country_ipv4.bin is
a pre-built, sorted table of (start_ip, end_ip, country_code) ranges baked
into the repo, and lookups are a pure in-process binary search.

Data: DB-IP's free "IP to Country Lite" database, redistributed under
CC-BY 4.0 via https://github.com/sapics/ip-location-db (dbip-country).
IP geolocation by DB-IP: https://db-ip.com — re-download periodically
(it drifts as IP blocks get reassigned) from:
  https://raw.githubusercontent.com/sapics/ip-location-db/main/dbip-country/dbip-country-ipv4.csv

country_ipv4.bin format: records of struct ">II2s" (start_ip, end_ip,
2-letter country code), sorted by start_ip, no overlaps.
"""
from __future__ import annotations
import bisect
import ipaddress
import struct
from array import array
from pathlib import Path

DATA_FILE = Path(__file__).parent / "geoip_data" / "country_ipv4.bin"
RECORD_SIZE = 10  # 4 (start) + 4 (end) + 2 (country code)

_starts: array = array("I")
_ends: array = array("I")
_countries: list[str] = []
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    if DATA_FILE.exists():
        data = DATA_FILE.read_bytes()
        n = len(data) // RECORD_SIZE
        for i in range(n):
            start, end, cc = struct.unpack_from(">II2s", data, i * RECORD_SIZE)
            _starts.append(start)
            _ends.append(end)
            _countries.append(cc.rstrip(b"\x00").decode("ascii"))
    _loaded = True


def lookup_country(ip: str) -> str | None:
    """ISO-3166 alpha-2 country code for an IPv4 address, or None for
    private/reserved ranges, IPv6, malformed input, or a gap in the data."""
    _load()
    try:
        addr = ipaddress.ip_address(ip)
        if addr.version != 4 or addr.is_private:
            return None
        ip_int = int(addr)
    except ValueError:
        return None
    i = bisect.bisect_right(_starts, ip_int) - 1
    if i < 0:
        return None
    if _starts[i] <= ip_int <= _ends[i]:
        return _countries[i]
    return None
