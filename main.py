# main.py
import asyncio
import config
import utils
import api
import watchlists
import time
import json
import os
from datetime import datetime

# File dove salviamo la memoria
CACHE_FILE = "sorare_memory.json"

# Cache in RAM
PLAYER_THRESHOLDS = {}       
ALL_TARGET_PLAYERS = []      

def load_cache():
    """Carica la memoria dal file JSON se esiste"""
    global PLAYER_THRESHOLDS
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                PLAYER_THRESHOLDS = json.load(f)
            print(f"💾 Memoria caricata: {len(PLAYER_THRESHOLDS)} giocatori già analizzati.")
        except Exception as e:
            print(f"⚠️ Errore caricamento memoria: {e}")

def save_cache():
    """Salva la memoria su file JSON"""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(PLAYER_THRESHOLDS, f, indent=4)
        # print("💾 Memoria salvata.")
    except Exception as e:
        print(f"⚠️ Errore salvataggio memoria: {e}")

async def build_target_list(token):
    print("\n🏗️ COSTRUZIONE LISTA BERSAGLI...")
    global ALL_TARGET_PLAYERS
    ALL_TARGET_PLAYERS = []
    
    # 1. Da Squadre
    print(f"   🌐 Scarico rose di {len(config.TARGET_TEAMS)} squadre...")
    for team_slug in config.TARGET_TEAMS:
        players = api.fetch_team_players(token, team_slug)
        ALL_TARGET_PLAYERS.extend(players)
        # Pausa minima per non bloccare tutto
        await asyncio.sleep(0.2)

    # 2. Da Watchlist Manuale
    if hasattr(watchlists, 'EXTRA_PLAYERS') and watchlists.EXTRA_PLAYERS:
        print(f"   📝 Aggiungo {len(watchlists.EXTRA_PLAYERS)} manuali...")
        ALL_TARGET_PLAYERS.extend(watchlists.EXTRA_PLAYERS)
        
    ALL_TARGET_PLAYERS = list(set(ALL_TARGET_PLAYERS))
    print(f"✅ Totale Giocatori: {len(ALL_TARGET_PLAYERS)}")

async def analyze_market_history(token):
    print("\n🧠 CALCOLO MEDIE CLASSIC...")
    
    # Carichiamo la memoria precedente
    load_cache()
    
    count = 0
    new_analysis = 0
    
    for player_slug in ALL_TARGET_PLAYERS:
        count += 1
        
        # Se lo conosciamo già, saltiamo (usiamo la cache)
        if player_slug in PLAYER_THRESHOLDS: 
            continue
            
        # Altrimenti scarichiamo storico
        history = api.fetch_sales_history(token, player_slug, "limited")
        targets = utils.calculate_smart_thresholds(history)
        
        if targets:
            PLAYER_THRESHOLDS[player_slug] = targets
            new_analysis += 1
        
        # Feedback visivo
        print(f"\r   ⏳ Analisi: {count}/{len(ALL_TARGET_PLAYERS)} completati...", end="", flush=True)
            
        # Salviamo su file ogni 20 giocatori nuovi analizzati
        if new_analysis > 0 and new_analysis % 20 == 0:
            save_cache()
            
        await asyncio.sleep(0.4) # Rispetto API
        
    # Salvataggio finale
    save_cache()
    print("\n🧠 Analisi Completata e Salvata.\n")

async def run_classic_sniper(token, rarity):
    print(f"\n🔫 CECCHINO MANAGER CLASSIC ({rarity.upper()})...")
    
    start_time = time.time()
    checks = 0
    alerts = 0
    
    for player_slug in ALL_TARGET_PLAYERS:
        thresholds = PLAYER_THRESHOLDS.get(player_slug)
        if not thresholds: continue 
        
        floor_price = api.fetch_classic_floor(token, player_slug, rarity)
        checks += 1
        
        if floor_price == "EXPIRED":
            token = api.login(config.SORARE_EMAIL, config.SORARE_PASSWORD)
            return token 
        
        if floor_price:
            avg = thresholds['avg']
            if avg > 0:
                discount = ((avg - floor_price) / avg) * 100
                
                # NOTIFICA SCONTO IMPORTANTE
                if discount >= config.SNIPER_THRESHOLD_PERCENT:
                    print(f"   🔥 {player_slug}: {floor_price:.2f}€ (Sconto {discount:.1f}%)")
                    alerts += 1
                    
                    # Logica notifica
                    sniper_thresholds = {'buy': floor_price + 0.1, 'sell': None, 'avg': avg}
                    await utils.check_and_notify(player_slug, floor_price, sniper_thresholds, rarity)
        
        # Leggero delay per non essere bannati (visto che sono 2000 richieste)
        await asyncio.sleep(0.3)
        
    duration = time.time() - start_time
    print(f"   ⏱️ Giro completato in {duration:.0f}s. Controllati {checks}, Trovati {alerts}.")
    return token

async def main():
    print(f"🚀 SorareBot PRO (Persistent Memory) - Avvio...")
    utils.update_eth_price()
    
    token = api.login(config.SORARE_EMAIL, config.SORARE_PASSWORD)
    if not token: return

    # 1. Costruisci lista (Squadre + Manuali)
    await build_target_list(token)
    
    # 2. Studia i prezzi (o carica da file)
    await analyze_market_history(token)
    
    # 3. Ciclo Infinito
    while True:
        utils.update_eth_price()
        
        token = await run_classic_sniper(token, "limited")
        
        # token = await run_classic_sniper(token, "rare") # Decommenta per Rare
        
        print(f"\n💤 Pausa {config.INTERVALLO_CONTROLLO}s...")
        await asyncio.sleep(config.INTERVALLO_CONTROLLO)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: 
        save_cache() # Salva se premi CTRL+C
        print("\n👋 Bot fermato.")