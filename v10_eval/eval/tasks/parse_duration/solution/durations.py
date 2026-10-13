import re

UNITS = {"h": 3600, "m": 60, "s": 1}
PATTERN = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_duration(text):
    """'90s' -> 90, '2h' -> 7200, '1h30m' -> 5400"""
    text = text.strip()
    m = PATTERN.fullmatch(text)
    if not text or not m or not any(m.groups()):
        raise ValueError(f"invalid duration: {text!r}")
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s
