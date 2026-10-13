def paginate(items, page, per_page=10):
    """Return the items on `page` (pages are 1-indexed)."""
    if page < 1:
        raise ValueError("page must be >= 1")
    start = page * per_page
    return items[start:start + per_page]


def page_count(items, per_page=10):
    return len(items) // per_page
