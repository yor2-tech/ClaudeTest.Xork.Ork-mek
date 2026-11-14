"""Wachhund: verbindet Frigate-Personenerkennung, Bluetooth-Anhänger und Signal.

Ablauf:
  1. Frigate erkennt eine Person, die eine überwachte Zone betritt (MQTT "frigate/events").
  2. Wachhund wartet kurz (Bluetooth meldet sich manchmal ein paar Sekunden später).
  3. War in der Zwischenzeit ein bekannter Anhänger in der Nähe (ESPresense,
     MQTT "espresense/devices/..."), passiert nichts – die Person ist bekannt.
  4. Sonst: Schnappschuss von Frigate holen und per Signal verschicken.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable

import paho.mqtt.client as mqtt
import requests
import yaml

log = logging.getLogger("wachhund")

Uhr = Callable[[], float]


class Anwesenheit:
    """Merkt sich, wann welcher bekannte Anhänger zuletzt nah genug war."""

    def __init__(self, bekannte: dict[str, str], max_distanz_m: float,
                 gueltig_s: float, uhr: Uhr = time.monotonic):
        self.bekannte = bekannte  # Anhänger-ID -> Name der Person
        self.max_distanz_m = max_distanz_m
        self.gueltig_s = gueltig_s
        self._uhr = uhr
        self._zuletzt: dict[str, float] = {}
        self._lock = threading.Lock()

    def melde(self, anhaenger_id: str, distanz_m: float | None) -> None:
        if anhaenger_id not in self.bekannte:
            return
        if distanz_m is None or distanz_m > self.max_distanz_m:
            return
        with self._lock:
            self._zuletzt[anhaenger_id] = self._uhr()
        log.debug("Anhänger %s (%s) in %.1f m gesehen",
                  anhaenger_id, self.bekannte[anhaenger_id], distanz_m)

    def anwesend(self) -> list[str]:
        """Namen aller Personen, deren Anhänger kürzlich in der Nähe war."""
        jetzt = self._uhr()
        with self._lock:
            return sorted(self.bekannte[a] for a, t in self._zuletzt.items()
                          if jetzt - t <= self.gueltig_s)


class Ereignisfilter:
    """Entscheidet, welche Frigate-Ereignisse überhaupt relevant sind."""

    def __init__(self, zonen: set[str], cooldown_s: float, uhr: Uhr = time.monotonic):
        self.zonen = zonen
        self.cooldown_s = cooldown_s
        self._uhr = uhr
        self._bearbeitet: set[str] = set()
        self._letzter_alarm: dict[str, float] = {}
        self._lock = threading.Lock()

    def neue_person(self, nachricht: dict) -> dict | None:
        """Gibt das Ereignis zurück, wenn eine Person erstmals eine Zone betritt."""
        nach = nachricht.get("after") or {}
        eid = nach.get("id")
        if not eid:
            return None
        with self._lock:
            if nachricht.get("type") == "end":
                self._bearbeitet.discard(eid)
                return None
            if nach.get("label") != "person" or nach.get("false_positive"):
                return None
            if not self.zonen & set(nach.get("entered_zones") or []):
                return None
            if eid in self._bearbeitet:
                return None
            self._bearbeitet.add(eid)
            return nach

    def darf_alarmieren(self, kamera: str) -> bool:
        """Verhindert Nachrichtenfluten: höchstens ein Alarm pro Kamera je Cooldown."""
        jetzt = self._uhr()
        with self._lock:
            letzter = self._letzter_alarm.get(kamera)
            if letzter is not None and jetzt - letzter < self.cooldown_s:
                return False
            self._letzter_alarm[kamera] = jetzt
            return True


class Signal:
    """Verschickt Nachrichten über signal-cli-rest-api."""

    def __init__(self, url: str, absender: str, empfaenger: list[str]):
        self.url = url.rstrip("/")
        self.absender = absender
        self.empfaenger = empfaenger

    def sende(self, text: str, bild: bytes | None = None) -> None:
        daten: dict = {"message": text, "number": self.absender,
                       "recipients": self.empfaenger}
        if bild:
            b64 = base64.b64encode(bild).decode()
            daten["base64_attachments"] = [
                f"data:image/jpeg;filename=person.jpg;base64,{b64}"]
        for versuch in range(1, 4):
            try:
                r = requests.post(f"{self.url}/v2/send", json=daten, timeout=30)
                r.raise_for_status()
                log.info("Signal-Nachricht verschickt")
                return
            except requests.RequestException as fehler:
                log.warning("Signal-Versand fehlgeschlagen (Versuch %d/3): %s",
                            versuch, fehler)
                time.sleep(2 * versuch)
        log.error("Signal-Nachricht konnte nicht verschickt werden")


class Frigate:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def bild(self, ereignis_id: str | None, kamera: str) -> bytes | None:
        adressen = []
        if ereignis_id:
            adressen.append(f"{self.url}/api/events/{ereignis_id}/snapshot.jpg?bbox=1")
        adressen.append(f"{self.url}/api/{kamera}/latest.jpg?bbox=1")
        for adresse in adressen:
            try:
                r = requests.get(adresse, timeout=10)
                if r.ok and r.content:
                    return r.content
            except requests.RequestException as fehler:
                log.warning("Bild von %s nicht abrufbar: %s", adresse, fehler)
        return None


class Wachhund:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        anw = cfg["anwesenheit"]
        self.anwesenheit = Anwesenheit(
            bekannte=anw.get("anhaenger") or {},
            max_distanz_m=float(anw.get("max_distanz_m", 8)),
            gueltig_s=float(anw.get("gueltig_s", 180)),
        )
        alarm = cfg["alarm"]
        self.filter = Ereignisfilter(
            zonen=set(alarm.get("zonen") or []),
            cooldown_s=float(alarm.get("cooldown_s", 60)),
        )
        self.wartezeit_s = float(alarm.get("wartezeit_s", 15))
        self.kameranamen: dict[str, str] = alarm.get("kameranamen") or {}
        self.link: str | None = alarm.get("link")
        self.frigate = Frigate(cfg["frigate"]["url"])
        sig = cfg["signal"]
        self.signal = Signal(sig["url"], sig["absender"], sig["empfaenger"])
        self.espresense_prefix = cfg["mqtt"].get("espresense_topic", "espresense/devices")

    # --- MQTT ---------------------------------------------------------------

    def starte(self) -> None:
        m = self.cfg["mqtt"]
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wachhund")
        if m.get("benutzer"):
            client.username_pw_set(m["benutzer"], m.get("passwort"))
        client.on_connect = self._verbunden
        client.on_message = self._nachricht
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        log.info("Verbinde mit MQTT %s:%s ...", m["host"], m.get("port", 1883))
        client.connect(m["host"], int(m.get("port", 1883)))
        client.loop_forever()

    def _verbunden(self, client, _userdata, _flags, grund, _props=None):
        if grund.is_failure:
            log.error("MQTT-Verbindung abgelehnt: %s", grund)
            return
        client.subscribe("frigate/events")
        client.subscribe(f"{self.espresense_prefix}/#")
        log.info("Wachhund ist scharf. Bekannte Anhänger: %s",
                 ", ".join(self.anwesenheit.bekannte.values()) or "keine")

    def _nachricht(self, _client, _userdata, msg):
        try:
            daten = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        if msg.topic == "frigate/events":
            ereignis = self.filter.neue_person(daten)
            if ereignis:
                log.info("Person in Zone erkannt (Kamera %s), prüfe Anhänger in %.0f s",
                         ereignis.get("camera"), self.wartezeit_s)
                threading.Timer(self.wartezeit_s, self._entscheide, args=(ereignis,)).start()
        elif msg.topic.startswith(self.espresense_prefix + "/"):
            teile = msg.topic[len(self.espresense_prefix) + 1:].split("/")
            if teile and isinstance(daten, dict):
                self.anwesenheit.melde(teile[0], daten.get("distance"))

    # --- Entscheidung -----------------------------------------------------

    def _entscheide(self, ereignis: dict) -> None:
        kamera = ereignis.get("camera", "?")
        bekannt = self.anwesenheit.anwesend()
        if bekannt:
            log.info("Person an Kamera %s – bekannt (%s), kein Alarm",
                     kamera, ", ".join(bekannt))
            return
        if not self.filter.darf_alarmieren(kamera):
            log.info("Person an Kamera %s – Alarm vor Kurzem schon gesendet", kamera)
            return
        self.alarmiere(kamera, ereignis.get("id"))

    def alarmiere(self, kamera: str, ereignis_id: str | None = None,
                  text: str | None = None) -> None:
        name = self.kameranamen.get(kamera, kamera)
        zeit = datetime.now().strftime("%d.%m. %H:%M")
        text = text or f"🚨 Unbekannte Person auf dem Grundstück\n📷 {name} · {zeit}"
        if self.link:
            text += f"\n{self.link}"
        log.warning("ALARM: %s", text.replace("\n", " | "))
        self.signal.sende(text, self.frigate.bild(ereignis_id, kamera))


def lade_konfiguration(pfad: str) -> dict:
    with open(pfad, encoding="utf-8") as f:
        # ${VARIABLE} im Text wird durch Umgebungsvariablen ersetzt (für Passwörter)
        return yaml.safe_load(os.path.expandvars(f.read()))


def main() -> None:
    parser = argparse.ArgumentParser(description="Wachhund – Personenalarm per Signal")
    parser.add_argument("--config", default=os.environ.get("WACHHUND_CONFIG", "/config/config.yaml"))
    parser.add_argument("--test", metavar="KAMERA",
                        help="Testnachricht mit aktuellem Bild dieser Kamera senden und beenden")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    hund = Wachhund(lade_konfiguration(args.config))
    if args.test:
        hund.alarmiere(args.test, text=f"✅ Wachhund-Test – Kamera {args.test}")
        return
    hund.starte()


if __name__ == "__main__":
    main()
