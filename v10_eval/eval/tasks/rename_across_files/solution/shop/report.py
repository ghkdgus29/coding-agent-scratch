from shop.orders import compute_total


def summary(orders):
    totals = [compute_total(items) for items in orders]
    return {"count": len(totals), "revenue": round(sum(totals), 2)}
