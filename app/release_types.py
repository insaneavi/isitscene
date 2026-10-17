"""Mutually exclusive Scene source classification."""
import re
TYPES = ("UHD BluRay", "BluRay", "WEB-DL", "WEBRip", "HDTV", "DVD", "Other")
def classify(name: str) -> str:
    n = name.casefold()
    if re.search(r"(?:^|[._ -])(?:web[._ -]?dl|webdl)(?:[._ -]|$)", n): return "WEB-DL"
    if re.search(r"(?:^|[._ -])webrip(?:[._ -]|$)", n): return "WEBRip"
    if re.search(r"(?:^|[._ -])(?:uhd|ultrahd)(?:[._ -]|$)", n) and re.search(r"bluray|bdrip|bdremux|remux", n): return "UHD BluRay"
    if re.search(r"(?:^|[._ -])(?:bluray|bdrip|brrip|bdremux|remux)(?:[._ -]|$)", n): return "BluRay"
    if re.search(r"(?:^|[._ -])(?:hdtv|pdtv)(?:[._ -]|$)", n): return "HDTV"
    if re.search(r"(?:^|[._ -])(?:dvdrip|dvd5|dvd9|dvd)(?:[._ -]|$)", n): return "DVD"
    return "Other"
