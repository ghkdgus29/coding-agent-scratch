from shop.orders import calc_total


def summary(orders):
    totals = [calc_total(items) for items in orders]
    return {"count": len(totals), "revenue": round(sum(totals), 2)}
