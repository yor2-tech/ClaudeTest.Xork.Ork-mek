from pathlib import Path

import pytest

from wachhund import Anwesenheit, Ereignisfilter, Wachhund, lade_konfiguration

BEISPIEL = str(Path(__file__).parent.parent / "config" / "wachhund.example.yaml")


class FalscheUhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def person(eid="e1", zonen=("grundstueck",), typ="update", **extra):
    return {"type": typ, "after": {"id": eid, "camera": "einfahrt", "label": "person",
                                   "entered_zones": list(zonen), **extra}}


def test_bekannter_anhaenger_in_der_naehe():
    uhr = FalscheUhr()
    a = Anwesenheit({"tag:mama": "Mama"}, max_distanz_m=8, gueltig_s=180, uhr=uhr)
    a.melde("tag:mama", 3.0)
    assert a.anwesend() == ["Mama"]
    uhr.t += 181
    assert a.anwesend() == []


def test_anhaenger_zu_weit_weg_oder_unbekannt_zaehlt_nicht():
    a = Anwesenheit({"tag:mama": "Mama"}, max_distanz_m=8, gueltig_s=180, uhr=FalscheUhr())
    a.melde("tag:mama", 12.0)
    a.melde("tag:mama", None)
    a.melde("tag:fremd", 1.0)
    assert a.anwesend() == []


def test_person_in_zone_wird_nur_einmal_gemeldet():
    f = Ereignisfilter({"grundstueck"}, cooldown_s=60, uhr=FalscheUhr())
    assert f.neue_person(person()) is not None
    assert f.neue_person(person()) is None  # gleiches Ereignis, nächstes Update
    assert f.neue_person(person(eid="e2")) is not None


def test_irrelevante_ereignisse_werden_ignoriert():
    f = Ereignisfilter({"grundstueck"}, cooldown_s=60, uhr=FalscheUhr())
    assert f.neue_person(person(zonen=())) is None  # nur auf dem Gehweg
    assert f.neue_person(person(zonen=("strasse",))) is None
    assert f.neue_person(person(false_positive=True)) is None
    katze = person()
    katze["after"]["label"] = "cat"
    assert f.neue_person(katze) is None
    assert f.neue_person({"type": "new"}) is None


def test_ende_vergisst_ereignis():
    f = Ereignisfilter({"grundstueck"}, cooldown_s=60, uhr=FalscheUhr())
    f.neue_person(person())
    f.neue_person(person(typ="end"))
    assert f.neue_person(person()) is not None


def test_cooldown_pro_kamera():
    uhr = FalscheUhr()
    f = Ereignisfilter({"grundstueck"}, cooldown_s=60, uhr=uhr)
    assert f.darf_alarmieren("einfahrt")
    assert not f.darf_alarmieren("einfahrt")
    assert f.darf_alarmieren("garten")
    uhr.t += 61
    assert f.darf_alarmieren("einfahrt")


@pytest.mark.parametrize("passwort", ["123456", "geheim: 1", "@start", "ja", "a#b $x"])
def test_konfiguration_uebernimmt_werte_aus_env_als_text(monkeypatch, passwort):
    monkeypatch.setenv("MQTT_USER", "wachhund")
    monkeypatch.setenv("MQTT_PASSWORD", passwort)
    monkeypatch.setenv("SIGNAL_BOT_NUMMER", "+4915112345678")
    cfg = lade_konfiguration(BEISPIEL)
    assert cfg["mqtt"]["passwort"] == passwort
    assert cfg["signal"]["absender"] == "+4915112345678"
    Wachhund(cfg)  # Beispielkonfiguration ist gültig


def test_fehlende_variable_wird_gemeldet(monkeypatch):
    monkeypatch.delenv("MQTT_USER", raising=False)
    with pytest.raises(SystemExit, match="MQTT_USER"):
        lade_konfiguration(BEISPIEL)


def test_nummer_ohne_anfuehrungszeichen_wird_gemeldet(monkeypatch, tmp_path):
    for name in ("MQTT_USER", "MQTT_PASSWORD", "SIGNAL_BOT_NUMMER"):
        monkeypatch.setenv(name, "+49151")
    datei = tmp_path / "w.yaml"
    datei.write_text(open(BEISPIEL, encoding="utf-8").read()
                     .replace('"+491701234567"', "+491701234567"), encoding="utf-8")
    with pytest.raises(SystemExit, match="Anführungszeichen"):
        Wachhund(lade_konfiguration(str(datei)))
