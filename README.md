# 📦 Pi Archiver

Raspberry Pi alapú fájl archiváló szerver – USB SSD-ről TrueNAS-ra másolás webes felületen keresztül, mobilra optimalizálva.

## Funkciók

- **USB auto-felismerés** – automatikusan észleli és mountolja a csatlakoztatott SSD-ket
- **Webes fájlböngésző** – mobilra optimalizált felület szűrőkkel (fotó/videó/vegyes)
- **Archived jelölés** – a már átmásolt fájlok ✅ jelöléssel láthatók, nem kell duplán másolni
- **Egyedi fájl kiválasztás** – nem a teljes mappaszerkezetet másolja, csak a kijelölt fájlokat
- **Mobilnet-reziliens átvitel** – rsync `--partial` + automatikus újrapróbálkozás (konfig. retry szám)
- **Speedtest** – 10-15mp-es írási teszt a NAS-ra, ETA becslés másolás előtt
- **Discord értesítések** – webhook alapú progress, indulás, befejezés, hiba értesítések
- **Valós idejű progress** – sebesség, átlagos sebesség, ETA, fájlonkénti haladás
- **Beállítható cél útvonal** – webes felületen konfigurálható TrueNAS kapcsolat és cél mappa

## Architektúra

```
[Telefon] → Mobilnet/WiFi → [Tailscale VPN (direkt)] → [Raspberry Pi :5000]
                                                               │
                                                      [USB SSD] → rsync → [SMB mount] → [TrueNAS]
                                                               │
                                                      Discord Webhook ← értesítések
```

| Komponens | Technológia |
|-----------|-------------|
| Backend | Python + Flask |
| Frontend | HTML + Tailwind CSS + vanilla JS |
| Átvitel | SMB mount (mount.cifs) + rsync --partial |
| Értesítések | Discord webhook |
| VPN | Tailscale (direkt kapcsolat) |
| Service | systemd |

## Telepítés

### Előfeltételek

- Raspberry Pi (3/4/5) RPI OS Lite-tal
- Tailscale telepítve és konfigurálva (direkt kapcsolat ajánlott)
- TrueNAS szerveren SMB share beállítva

### Lépések

```bash
git clone https://github.com/FELHASZNALO/pi-archiver.git
cd pi-archiver
chmod +x install.sh update.sh
sudo ./install.sh
```

### Első beállítás

1. Nyisd meg: `http://<pi-tailscale-ip>:5000`
2. ⚙️ **Beállítások** → TrueNAS adatok megadása → Kapcsolat tesztelése
3. Átviteli beállítások (retry szám, sávszélesség limit)
4. Discord webhook URL beállítása (opcionális)

## Használat

### Fájlok másolása

1. Csatlakoztasd az USB SSD-t
2. Nyisd meg a webes felületet telefonon
3. Válassz szűrőt (Fotó/Videó/Mind)
4. Jelöld ki a fájlokat – a már archiváltak ✅ jelöléssel láthatók
5. **📤 Másolás** gomb

### Speedtest (másolás előtt)

1. Jelölj ki fájlokat
2. Nyomd meg a **🏎️ Teszt** gombot az átviteli sávon
3. ~12 másodpercig ír teszt adatot a NAS-ra
4. Megmutatja az írási sebességet és becsült ETA-t a kijelölt fájlokhoz
5. Discord értesítést is küld az eredményről (ha be van kapcsolva)

### Discord értesítések

Webhook-on keresztül küld értesítéseket:
- 🚀 Másolás indítása (fájlszám, méret)
- 📤 Progress a beállított %-onként (5/10/25/50%)
- ✅ Befejezés (idő, átlagos sebesség)
- ❌ Hiba (hibaüzenet)
- 🏎️ Speedtest eredmény (sebesség, ETA)

### Mobilnet tippek

- A **direkt Tailscale kapcsolat** (nem DERP) sokkal stabilabb
- Az rsync automatikusan újrapróbálja ha megszakad (alapból 5x)
- A `--partial` opció biztosítja hogy félbehagyott fájlokat folytatja
- Sávszélesség limit beállítható ha instabil a kapcsolat

## Frissítés

```bash
cd pi-archiver
git pull origin main
sudo ./update.sh
```

## Fájlstruktúra

```
pi-archiver/
├── app/
│   ├── main.py              # Flask app entry point
│   ├── routes.py             # API endpoints
│   ├── config.py             # Config + archive tracking (JSON)
│   ├── storage.py            # USB detection, file listing
│   ├── transfer.py           # SMB mount + rsync with retry
│   ├── speedtest.py          # NAS write speed test
│   ├── discord_notify.py     # Discord webhook notifications
│   └── templates/
│       └── index.html        # Mobile-first frontend
├── systemd/
│   └── pi-archiver.service
├── install.sh
├── update.sh
├── requirements.txt
└── README.md
```

## Licensz

MIT
