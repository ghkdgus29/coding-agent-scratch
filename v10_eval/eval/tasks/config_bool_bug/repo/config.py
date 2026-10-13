DEFAULTS = {"debug": False, "workers": 4, "name": "app", "ratio": 0.5}


def load_config(raw):
    """Build a config from string values (e.g. environment variables), converting to the default's type."""
    config = dict(DEFAULTS)
    for key, value in raw.items():
        if key not in DEFAULTS:
            continue
        config[key] = type(DEFAULTS[key])(value)
    return config
