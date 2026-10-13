import sys

from shop import orders


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    items = [(float(p), int(q)) for p, q in (a.split("x") for a in argv)]
    print(orders.compute_total(items, tax_rate=0.1))
