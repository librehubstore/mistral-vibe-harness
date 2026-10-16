def round_eur(amount):
    return round(amount, 2)


def total_ttc(ht, vat_rate):
    return round_eur(ht * (1 + vat_rate))
