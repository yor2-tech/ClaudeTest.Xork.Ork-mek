# Wachhund – Kameraalarm mit Personenerkennung

Erkennt Personen auf dem Grundstück und schickt **eine Signal-Nachricht mit Foto**.
Familie und Freunde bekommen einen kleinen **Bluetooth-Anhänger** für den Schlüsselbund.
Ist so ein Anhänger in der Nähe, gibt es keinen Alarm. Alles läuft bei euch zu Hause,
ohne Cloud und ohne Abo.

```
Kameras ──► Frigate (erkennt Personen) ──┐
                                         ├──► Wachhund ──► Signal-Nachricht mit Bild
ESP32 am Eingang (sieht Anhänger) ───────┘     „Fremder oder bekannt?“
```

## Was ist Frigate?

[Frigate](https://frigate.video) ist eine kostenlose Open-Source-Videoüberwachung.
Sie läuft auf einem kleinen Rechner bei euch zu Hause, nimmt die Kameras auf und erkennt
per KI, ob im Bild **eine Person** ist und nicht nur eine Katze, ein Schatten oder Regen.
Im Bild zeichnet man eine **Zone** ein (z. B. „Grundstück ohne Gehweg“). Nur Personen
in dieser Zone zählen. Wer auf dem Gehweg vorbeiläuft, löst also nichts aus.
Über eine Weboberfläche seht ihr Live-Bilder und alle Aufnahmen.

Den Teil, den Frigate nicht kann, übernimmt der **Wachhund**
([`wachhund/wachhund.py`](wachhund/wachhund.py)): Er gleicht ab, ob gerade ein bekannter
Anhänger in der Nähe ist, und verschickt sonst die Signal-Nachricht.

## Einkaufsliste (Richtpreise)

| Was | Beispiel | ca. Preis |
|---|---|---|
| 2–4 Kameras mit **PoE + RTSP** | Reolink RLC-520A (5 MP) oder RLC-810A (4K) | 50–100 € pro Stück |
| PoE-Switch (Strom + Netzwerk über ein Kabel) | TP-Link TL-SG1005P / TL-SG1008P | 40–70 € |
| Mini-PC mit Intel N100, 16 GB RAM, SSD ≥ 512 GB | z. B. Beelink, Minisforum, GMKtec | 150–220 € |
| 1–2 ESP32-Boards + USB-Netzteil (Bluetooth-Empfänger am Eingang) | ESP32-WROOM-32 DevKit | je ~10 € |
| Bluetooth-Anhänger (**iBeacon** mit fester ID) | „iBeacon Schlüsselanhänger“, z. B. Feasycom, Minew | je 8–15 € |
| Prepaid-SIM für die Signal-Bot-Nummer | beliebiger Anbieter | ~5–10 € |

**Bei den Kameras unbedingt auf „RTSP“ achten.** Nur solche Kameras lassen sich direkt
anbinden. Reine Cloud-Kameras (z. B. Ring, Arlo, Blink) funktionieren damit **nicht**.
Am stabilsten sind Kameras mit **Netzwerkkabel (PoE)** statt WLAN.

### Warum keine AirTags?
AirTags lassen sich hierfür leider nicht nutzen. Apple ändert ihre Funkkennung
regelmäßig (Schutz gegen Stalking), deshalb kann man ein bestimmtes AirTag nicht
wiedererkennen. Außerdem piepen AirTags und warnen das iPhone der tragenden Person,
wenn sie nicht der Besitzerin oder dem Besitzer gehören. Normale iBeacon-Anhänger
senden dagegen immer dieselbe Kennung und halten mit einer Knopfzelle meist 1–2 Jahre.

## Einrichtung

### 1. Netzwerk
- Kameras an den PoE-Switch, den Switch und den Mini-PC an den Router.
- Im Router (z. B. Fritz!Box) Kameras und Mini-PC eine **feste IP** geben.
- Empfohlen: den Kameras im Router den **Internetzugang sperren**
  (Fritz!Box: *Internet > Filter > Kindersicherung*). Die Bilder bleiben dann bei euch.
- Für jede Kamera einen Benutzer mit Passwort anlegen (in der Hersteller-App bzw.
  im Browser der Kamera). Danach braucht ihr die Hersteller-App nicht mehr.

### 2. Mini-PC
Debian oder Ubuntu Server installieren, dann [Docker](https://docs.docker.com/engine/install/) installieren. Anschließend:

```bash
git clone <dieses-repo> wachhund && cd wachhund
cp .env.example .env                        # Passwörter eintragen
cp config/wachhund.example.yaml config/wachhund.yaml
nano .env

# MQTT-Benutzer anlegen (gleicher Name/Passwort wie in .env)
docker compose run --rm mosquitto mosquitto_passwd -c -b /mosquitto/config/passwd wachhund DEIN-PASSWORT
```

### 3. Frigate einrichten
- In [`frigate/config.yml`](frigate/config.yml) die IP-Adressen der Kameras eintragen.
  Die Stream-Pfade im Beispiel gelten für Reolink. Bei anderen Marken stehen sie in der
  [Frigate-Kameraliste](https://docs.frigate.video/configuration/camera_specific).
- Starten: `docker compose up -d frigate mosquitto`
- Im Browser `https://<IP-des-Mini-PCs>:8971` öffnen. Das Admin-Passwort steht beim
  ersten Start im Log: `docker compose logs frigate | grep -i password`
- Unter *Einstellungen > Masken & Zonen* pro Kamera die Zone **`grundstueck`**
  einzeichnen (nur euer Grundstück, ohne Gehweg und Straße) und die Koordinaten in
  `frigate/config.yml` übernehmen.

### 4. Signal einrichten
Der Wachhund braucht eine **eigene Nummer** (die Prepaid-SIM). Würde er von eurer eigenen
Nummer senden, landen die Alarme als „Notiz an mich“ und das Handy klingelt **nicht**.

```bash
docker compose up -d signal
# 1. Captcha holen: https://signalcaptchas.org/registration/generate.html
#    (nach dem Lösen den Link „Open Signal“ kopieren, beginnt mit signalcaptcha://)
curl -X POST -H "Content-Type: application/json" \
  -d '{"captcha":"signalcaptcha://..."}' \
  http://localhost:8080/v1/register/+4915112345678
# 2. Der Code kommt per SMS auf die Bot-SIM (SIM dafür kurz in ein beliebiges Handy stecken):
curl -X POST http://localhost:8080/v1/register/+4915112345678/verify/123456
```

Dann in `config/wachhund.yaml` unter `empfaenger` eure Handynummern eintragen und die
Bot-Nummer in Signal als Kontakt speichern.

### 5. Bluetooth-Empfänger (ESP32) und Anhänger
- ESP32 per USB an einen PC und mit **[ESPresense](https://espresense.com/firmware)**
  direkt aus dem Browser (Chrome/Edge) bespielen. Raum-Name z. B. `eingang`.
- Im WLAN des ESP32 seine Weboberfläche öffnen und MQTT eintragen
  (Server = IP des Mini-PCs, Benutzer/Passwort aus `.env`).
- Den ESP32 **nah am Hauseingang bzw. Tor** aufstellen (innen am Fenster reicht oft).
- Anhänger-IDs herausfinden: In der ESPresense-Oberfläche erscheinen alle Anhänger in
  der Nähe. Dort kann man jedem einen Namen geben (z. B. `tag:mama`). Diese IDs in
  `config/wachhund.yaml` unter `anwesenheit.anhaenger` eintragen.

### 6. Starten und testen
```bash
docker compose up -d --build
docker compose run --rm wachhund --test einfahrt     # schickt ein Testbild aufs Handy
docker compose logs -f wachhund                      # live mitlesen
```
Dann einmal **ohne** und einmal **mit** Anhänger übers Grundstück laufen.

## Wie der Wachhund entscheidet

1. Frigate meldet: Person hat die Zone `grundstueck` betreten.
2. Der Wachhund wartet **15 Sekunden**, weil sich Bluetooth manchmal etwas später meldet.
3. War in den letzten **3 Minuten** ein bekannter Anhänger **näher als 8 m** am Empfänger,
   gilt die Person als bekannt und es gibt **keinen Alarm**.
4. Sonst geht die Signal-Nachricht mit Foto raus. Danach kommt pro Kamera höchstens
   **eine Nachricht pro Minute**, damit das Handy nicht dauernd vibriert.

Alle Werte lassen sich in `config/wachhund.yaml` anpassen.

**Gut zu wissen:**
- Kommt jemand Bekanntes **zusammen mit** einer fremden Person, gibt es keinen Alarm.
  Der Anhänger sagt nur „jemand Bekanntes ist da“, nicht wer genau im Bild ist.
- **Schlüssel nicht in der Nähe des Bluetooth-Empfängers aufhängen.** Hängt ein
  Schlüsselbund mit Anhänger dauerhaft näher als 8 m am ESP32 (z. B. am Schlüsselbrett
  neben der Haustür), gilt immer jemand als „bekannt“ und es gibt **gar keinen Alarm**,
  auch nachts nicht. Den ESP32 dann weiter weg aufstellen oder `max_distanz_m` in
  `config/wachhund.yaml` verkleinern. Zum Prüfen: Schlüssel an ihren Platz hängen und
  im Log (`docker compose logs wachhund`) schauen, ob bei einem Testlauf übers
  Grundstück „bekannt“ gemeldet wird.
- Briefträger, Paketdienste usw. lösen Alarm aus. Das ist so gewollt. Aufgenommen wird
  trotzdem alles, ihr könnt es in Frigate nachsehen.
- Die Kennung eines Anhängers ließe sich technisch kopieren. Für einen Alarm, den
  Fremde nicht kennen, ist das ein kleines Risiko. Verliert jemand den Anhänger,
  einfach die ID aus der Konfiguration löschen.

## Rechtliches (Kurzfassung, keine Rechtsberatung)
In Deutschland dürfen private Kameras grundsätzlich **nur das eigene Grundstück** filmen,
nicht öffentlichen Gehweg, Straße oder Nachbargrundstücke. Mit **Privatsphäre-Masken**
in Frigate (*Masken & Zonen*) könnt ihr solche Bereiche schwärzen. Ein **Hinweisschild**
„Videoüberwachung“ am Eingang ist üblich und wirkt zusätzlich abschreckend.
Wer regelmäßig zu Besuch kommt, sollte von der Kamera wissen.

## Entwicklung
```bash
cd wachhund && pip install -r requirements.txt pytest && pytest
```
