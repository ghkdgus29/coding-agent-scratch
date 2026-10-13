DEFAULTS = {"debug": False, "workers": 4, "name": "app", "ratio": 0.5}
TRUE = {"true", "1", "yes", "on"}
FALSE = {"false", "0", "no", "off"}


def parse_bool(value):
    v = str(value).strip().lower()
    if v in TRUE:
        return True
    if v in FALSE:
        return False
    raise ValueError(f"invalid boolean: {value!r}")


def load_config(raw):
    """Build a config from string values (e.g. environment variables), converting to the default's type."""
    config = dict(DEFAULTS)
    for key, value in raw.items():
        if key not in DEFAULTS:
            continue
        default = DEFAULTS[key]
        config[key] = parse_bool(value) if isinstance(default, bool) else type(default)(value)
    return config
