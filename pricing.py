"""Conversione valute e calcolo del prezzo di riferimento."""
import statistics


def to_eur(amounts, rates):
    """Converte un MonetaryAmount di Sorare in euro. Restituisce None se non è possibile."""
    if not amounts:
        return None
    if amounts.get("eurCents"):
        return amounts["eurCents"] / 100
    if amounts.get("wei"):
        return int(amounts["wei"]) / 10**18 * rates["eth_eur"]
    if amounts.get("usdCents"):
        return amounts["usdCents"] / 100 * rates["eth_eur"] / rates["eth_usd"]
    return None


def compute_stats(prices):
    """Mediana delle vendite: non si fa ingannare da poche vendite molto alte o molto basse
    (per esempio carte con numero di maglia o #1, vendute a prezzi fuori mercato)."""
    prices = sorted(p for p in prices if p and p > 0.01)
    if not prices:
        return {"count": 0, "median": None, "p25": None}
    p25 = statistics.quantiles(prices, n=4)[0] if len(prices) >= 4 else prices[0]
    return {"count": len(prices), "median": statistics.median(prices), "p25": p25}


def evaluate_deal(price, stats, discount_percent, min_discount_eur, min_sales):
    """Restituisce lo sconto in % se l'offerta è un affare, altrimenti None."""
    median = stats.get("median")
    if price is None or not median or stats.get("count", 0) < min_sales:
        return None
    discount_eur = median - price
    discount_pct = discount_eur / median * 100
    if discount_pct >= discount_percent and discount_eur >= min_discount_eur:
        return discount_pct
    return None
