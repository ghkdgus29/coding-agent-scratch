def slugify(text, max_length=50):
    """Turn `text` into a URL slug.

    Rules:
    - lowercase
    - accented latin letters become plain ASCII ("Café" -> "cafe")
    - any run of characters that are not a-z or 0-9 becomes a single "-"
    - no leading or trailing "-"
    - cut to at most `max_length` characters, without leaving a trailing "-"
    - an empty result returns "n-a"
    """
    raise NotImplementedError


def title_case(text):
    return " ".join(w.capitalize() for w in text.split())
