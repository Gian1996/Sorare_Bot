"""Bilancio degli avvisi: quante carte segnalate sono state comprate davvero, e quanto in fretta.

Lo stato vero di ogni offerta (comprata, ritirata, scaduta, ancora in vendita) e i suoi orari
li chiedo a Sorare, così il conto è giusto anche per le offerte chiuse mentre il bot era spento.
Le offerte chiuse non cambiano più: il loro esito resta salvato in notified.json.
"""
import re
import statistics
from datetime import datetime, timezone

DAY = 86400
CLOSED = ("accepted", "cancelled", "ended")
BANDS = [(0, 30, "sotto il 30%"), (30, 50, "30-50%"), (50, float("inf"), "50% e oltre")]
SPEED = [(0, "prima dell'avviso"), (60, "entro 1 min"), (600, "entro 10 min"), (3600, "entro 1 h"), (float("inf"), "dopo")]


def _ts(iso):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() if iso else None


def duration(seconds):
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 2 * DAY:
        return f"{seconds / 3600:.1f} h".replace(".0 h", " h")
    return f"{seconds / DAY:.0f} gg"


def recipients(entry, admin_id):
    """{chat_id: id messaggio}; il formato vecchio era solo per l'amministratore."""
    return entry.get("msgs") or {admin_id: entry.get("msg_id")}


def discount_of(entry):
    """Sconto sul riferimento al momento dell'avviso (dal testo, per le notifiche vecchie)."""
    if entry.get("discount") is not None:
        return entry["discount"]
    match = re.search(r"Sconto: <b>-(\d+)%", entry.get("text", ""))
    return float(match.group(1)) if match else None


async def refresh_outcomes(client, entries):
    """Chiede a Sorare l'esito delle offerte non ancora chiuse e lo salva in entry["outcome"]."""
    todo = {oid: e for oid, e in entries.items() if (e.get("outcome") or {}).get("status") not in CLOSED}
    if not todo:
        return False
    found = await client.offers_status(list(todo))
    for oid, entry in todo.items():
        offer = found.get(oid)
        if not offer:
            continue
        end = offer["acceptedAt"] or offer["cancelledAt"] or (offer["endDate"] if offer["status"] == "ended" else None)
        entry["outcome"] = {"status": offer["status"], "start": _ts(offer["startDate"]), "end": _ts(end)}
    return True


def rows(notified, chat_id, admin_id, since):
    """Gli avvisi ricevuti da chat_id dopo `since`, già pronti per il riepilogo."""
    out = []
    for entry in notified.values():
        if not isinstance(entry, dict) or entry.get("ts", 0) < since or chat_id not in recipients(entry, admin_id):
            continue
        outcome = entry.get("outcome") or {}
        out.append({
            "kind": entry.get("kinds", {}).get(chat_id, "deal"),
            "discount": discount_of(entry),
            "sent": entry["ts"],
            "status": outcome.get("status", "unknown"),
            "start": outcome.get("start"),
            "end": outcome.get("end"),
        })
    return out


def _pct(n, total):
    return f"{n / total * 100:.0f}%" if total else "-"


def _sold_line(items):
    sold = [r for r in items if r["status"] == "accepted" and r["end"]]
    if not sold:
        return f"comprate 0/{len(items)}"
    times = statistics.median(r["end"] - r["sent"] for r in sold)
    return f"comprate {len(sold)}/{len(items)} ({_pct(len(sold), len(items))}) · in mediana dopo {duration(times)}"


def summary(items, days):
    period = "nell'ultimo giorno" if days == 1 else f"negli ultimi {days} giorni"
    if not items:
        return (f"📒 Nessun avviso {period}.\n"
                "Il bilancio si riempie man mano che arrivano affari e avvisi della watchlist.")
    count = lambda status: sum(1 for r in items if r["status"] == status)
    deals = [r for r in items if r["kind"] == "deal"]
    watch = [r for r in items if r["kind"] == "watch"]
    sold = [r for r in items if r["status"] == "accepted" and r["end"]]

    lines = [f"📒 <b>Bilancio {period}</b>",
             f"{len(items)} avvisi ({len(deals)} affari, {len(watch)} watchlist)", ""]
    lines.append(f"✅ Comprate: <b>{len(sold)}</b> ({_pct(len(sold), len(items))})")
    if sold:
        waits = [r["end"] - r["sent"] for r in sold]
        counts = {label: 0 for _, label in SPEED}
        for wait in waits:
            counts[next(label for limit, label in SPEED if wait <= limit)] += 1
        lines.append("   " + " · ".join(f"{n} {label}" for label, n in counts.items() if n))
        lines.append(f"   tempo mediano dall'avviso: {duration(statistics.median(waits))}")
    lines.append(f"❌ Ritirate: {count('cancelled')} ({_pct(count('cancelled'), len(items))})"
                 f" · ⌛ Scadute: {count('ended')} · 🟢 Ancora in vendita: {count('opened')}")
    if count("unknown"):
        lines.append(f"❔ Non trovate su Sorare: {count('unknown')}")

    delays = [r["sent"] - r["start"] for r in items if r["start"]]
    if delays:
        lines.append(f"⏱ Ritardo degli avvisi: mediana {duration(statistics.median(delays))}"
                     f" (massimo {duration(max(delays))}) dalla messa in vendita")

    if deals:
        lines += ["", "<b>Affari per sconto</b>"]
        for low, high, label in BANDS:
            band = [r for r in deals if r["discount"] is not None and low <= r["discount"] < high]
            if band:
                lines.append(f"• {label}: {_sold_line(band)}")
    if watch:
        lines += ["", f"<b>Watchlist</b>: {_sold_line(watch)}"]

    lines += ["", "<i>Una carta comprata pochi minuti dopo l'avviso era un affare vero. Se quasi tutte "
                  "restano in vendita per ore o vengono ritirate (spesso per rimetterle a un altro prezzo), "
                  "la soglia è troppo bassa: prova ad alzarla con /soglia.</i>"]
    return "\n".join(lines)
