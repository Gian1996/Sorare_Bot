import requests
import time
from telegram import Bot
import config

# Stato globale
CURRENT_ETH_PRICE = 3000.0
LAST_ETH_UPDATE = 0
NOTIFIED_PRICES = {}
LAST_NOTIFICATION_TIME = {}

bot = Bot(token=config.TELEGRAM_TOKEN)

def calculate_smart_thresholds(sales_history):
    """
    Riceve una lista di prezzi di vendita (es. [100, 105, 98, ...])
    Restituisce: Media, Target Buy (-20%), Target Sell (+20%)
    """
    if not sales_history:
        return None
    
    # 1. Calcolo Media
    average_price = sum(sales_history) / len(sales_history)
    
    # 2. Calcolo Soglie
    buy_target = average_price * 0.80  # -20%
    sell_target = average_price * 1.20 # +20%
    
    return {
        "avg": average_price,
        "buy": buy_target,
        "sell": sell_target
    }

def update_eth_price():
    global CURRENT_ETH_PRICE, LAST_ETH_UPDATE
    if time.time() - LAST_ETH_UPDATE < 600: return
    try:
        url = "https://api.coinbase.com/v2/prices/ETH-EUR/spot"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            CURRENT_ETH_PRICE = float(resp.json()['data']['amount'])
            LAST_ETH_UPDATE = time.time()
            print(f"💱 Tasso ETH: {CURRENT_ETH_PRICE:.2f} €")
    except: pass

def extract_price(offer):
    if not offer: return None
    amounts = offer.get('receiverSide', {}).get('amounts')
    if not amounts: return None
    
    if amounts.get('eurCents'):
        return amounts['eurCents'] / 100.0
    elif amounts.get('wei'):
        try:
            return (int(amounts['wei']) / 10**18) * CURRENT_ETH_PRICE
        except: pass
    return None

async def check_and_notify(player_slug, current_price, thresholds, rarity):
    # threshold ora contiene {'buy': X, 'sell': Y, 'avg': Z}
    now = time.time()
    buy_target = thresholds.get('buy')
    sell_target = thresholds.get('sell')
    avg_price = thresholds.get('avg', 0)
    
    # --- LOGICA BUY ---
    if buy_target and current_price <= buy_target:
        memory_key = f"{player_slug}_{rarity}_BUY"
        should_notify = False
        
        if memory_key not in NOTIFIED_PRICES:
            should_notify = True
        else:
            last_price = NOTIFIED_PRICES[memory_key]
            if current_price < last_price: should_notify = True # Notifica solo se scende ancora

        if should_notify:
            NOTIFIED_PRICES[memory_key] = current_price
            icon = "🟡" if rarity == "limited" else "🔴"
            # Calcoliamo lo sconto reale
            discount = ((avg_price - current_price) / avg_price) * 100
            
            msg = (f"🚨 **AFFARE SMART {icon} (COMPRA)**\n"
                   f"⚽ {player_slug}\n"
                   f"💶 **{current_price:.2f} €**\n"
                   f"📊 Media 3 mesi: {avg_price:.2f}€\n"
                   f"📉 Sconto: -{discount:.1f}% (Target: {buy_target:.2f}€)")
            try:
                await bot.send_message(chat_id=config.CHAT_ID, text=msg, parse_mode='Markdown')
            except: pass

    # --- LOGICA SELL ---
    if sell_target and current_price >= sell_target:
        memory_key = f"{player_slug}_{rarity}_SELL"
        should_notify = False
        
        if memory_key not in NOTIFIED_PRICES:
            should_notify = True
        else:
            last_price = NOTIFIED_PRICES[memory_key]
            if current_price > last_price: should_notify = True

        if should_notify:
            NOTIFIED_PRICES[memory_key] = current_price
            icon = "🟡" if rarity == "limited" else "🔴"
            profit = ((current_price - avg_price) / avg_price) * 100
            
            msg = (f"💰 **VENDE ORA {icon}**\n"
                   f"⚽ {player_slug}\n"
                   f"💶 **{current_price:.2f} €**\n"
                   f"📊 Media 3 mesi: {avg_price:.2f}€\n"
                   f"📈 Profitto: +{profit:.1f}% (Target: {sell_target:.2f}€)")
            try:
                await bot.send_message(chat_id=config.CHAT_ID, text=msg, parse_mode='Markdown')
            except: pass