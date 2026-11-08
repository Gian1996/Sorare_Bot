"""Conversione valute, prezzo di riferimento e regola dell'affare.

Il prezzo di riferimento è una MEDIANA PESATA delle vendite Classic: ogni vendita pesa
la metà ogni HALF_LIFE_DAYS giorni (oggi 1, dopo 3 giorni 0,5, dopo 6 giorni 0,25...).
Così un calo recente (infortunio, panchina) conta subito, e una singola vendita fuori
mercato non sposta il risultato come farebbe una media.
"""
import statistics
import time

DAY = 86400


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


def weighted_median(sales, now, half_life_days):
    """`sales`: lista di (timestamp, prezzo). Il peso di ogni vendita si dimezza ogni half_life_days."""
    weighted = sorted((price, 0.5 ** (max(0, now - ts) / DAY / half_life_days)) for ts, price in sales)
    half = sum(w for _, w in weighted) / 2
    cumulative = 0.0
    for price, weight in weighted:
        cumulative += weight
        if cumulative >= half:
            return price
    return weighted[-1][0]


def reference(sales, history_days, half_life_days, recent_n, now=None):
    """Prezzo di riferimento di un giocatore a partire dalle sue vendite (timestamp, prezzo)."""
    now = now or time.time()
    sales = sorted(((ts, p) for ts, p in sales if p and p > 0.01 and now - ts <= history_days * DAY),
                   reverse=True)  # dalla più recente
    if not sales:
        return {"count": 0, "median": None, "recent": [], "recent_median": None, "recent_age_days": None}
    recent = sales[:recent_n]
    return {
        "count": len(sales),
        "recent_age_days": (now - recent[-1][0]) / DAY,  # età della più vecchia tra le ultime N
        "median": weighted_median(sales, now, half_life_days),
        "recent": recent,
        "recent_median": statistics.median(p for _, p in recent),
    }


def evaluate_deal(price, ref, discount_percent, min_discount_eur, min_sales, recent_max_age_days=None):
    """Restituisce lo sconto in % se l'offerta è un affare, altrimenti None.

    Serve che il prezzo sia abbastanza sotto la mediana pesata E sotto le ultime vendite:
    se le ultime vendite sono già tutte basse (calo in corso), non è un affare.
    Se le ultime vendite sono troppo vecchie, il prezzo di oggi non si conosce: niente affare.
    """
    median = ref.get("median")
    if price is None or not median or ref.get("count", 0) < min_sales:
        return None
    if recent_max_age_days is not None and (ref.get("recent_age_days") or 0) > recent_max_age_days:
        return None
    if ref.get("recent_median") is not None and price >= ref["recent_median"]:
        return None
    discount_eur = median - price
    discount_pct = discount_eur / median * 100
    if discount_pct >= discount_percent and discount_eur >= min_discount_eur:
        return discount_pct
    return None


def ago(ts, now=None):
    """Età di una vendita in parole: "12 min fa", "5 h fa", "3 gg fa"."""
    seconds = max(0, (now or time.time()) - ts)
    if seconds < 3600:
        return f"{int(seconds // 60)} min fa"
    if seconds < DAY:
        return f"{int(seconds // 3600)} h fa"
    return f"{int(seconds // DAY)} gg fa"
