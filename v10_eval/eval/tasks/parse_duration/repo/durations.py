UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """'90s' -> 90, '2h' -> 7200"""
    text = text.strip()
    if not text or text[-1] not in UNITS:
        raise ValueError(f"invalid duration: {text!r}")
    return int(text[:-1]) * UNITS[text[-1]]
