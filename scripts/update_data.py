#!/usr/bin/env python3
"""Mise à jour mensuelle des données PHD depuis l'Open Data Paris.

Règles (voir la méthodologie du site) :
- Les entrées dont source != "auto" (A, B, F manuels, fiches paris.fr...) ne sont JAMAIS modifiées.
- Les entrées source == "auto" sont rafraîchies ; leur catégorie suit le type_ev (F ou C).
- Les nouveaux espaces sont ajoutés en "auto" (C ou F) et listés dans le rapport pour relecture.
- Les espaces auto disparus de l'Open Data sont retirés et listés.
Sortie : phd_data.json, index.html (données inline), rapport.md
"""
import json, re, sys, urllib.request, urllib.parse, unicodedata

API = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/espaces_verts/exports/json"
MIN_RECORDS = 2000  # garde-fou : un export tronqué ne doit jamais écraser les données

# type_ev -> catégorie PHD, déduit des 2 400 entrées auto existantes
F_TYPES = {"Décorations sur la voie publique", "Murs végétalisés", "Périphérique",
           "Jardinets décoratifs", "Cimetières"}
DEFAULT_CAT = "C"  # tout ce qui n'est pas référencé comme autorisé reste C

CANDIDATES = {
    "id": ["id_ev", "identifiant", "id", "ida3d_enb", "objectid"],
    "nom": ["nom_ev", "nom"],
    "type_ev": ["type_ev"],
    "categorie": ["categorie"],
    "surface": ["surface_totale_reelle", "poly_area", "surface"],
    "cloture": ["presence_cloture", "cloture"],
    "arr": ["arrondissement", "code_postal", "adresse_codepostal"],
}

def pick(rec, key):
    for f in CANDIDATES[key]:
        if f in rec and rec[f] is not None:
            return rec[f]
    return None

def fetch():
    req = urllib.request.Request(API, headers={"User-Agent": "paris-hates-dogs-updater"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)

def arr_of(v):
    if v is None: return None
    s = re.sub(r"\D", "", str(v))
    if len(s) == 5 and s.startswith("750"): return int(s[3:])
    if s.isdigit() and 1 <= int(s) <= 20: return int(s)
    return None

def latlon(rec):
    g = rec.get("geom_x_y") or rec.get("geo_point_2d") or {}
    lat = g.get("lat"); lon = g.get("lon")
    return (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)

def norm(t): return unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().strip()

def to_entry(rec):
    lat, lon = latlon(rec)
    t = pick(rec, "type_ev") or ""
    cat = "F" if t in F_TYPES else DEFAULT_CAT
    reason = ("Voirie, décoration ou infrastructure non utilisable" if cat == "F"
              else "Non référencé sur paris.fr — chiens non admis par défaut")
    return {"id": pick(rec, "id"), "nom": pick(rec, "nom"), "arr": arr_of(pick(rec, "arr")),
            "type_ev": t, "categorie": pick(rec, "categorie") or "", "surface": pick(rec, "surface"),
            "cloture": pick(rec, "cloture") or "", "lat": lat, "lon": lon, "cat_phd": cat,
            "reason": reason, "source": "auto", "paris_fr_url": ""}

def key(e):  # l'id 0 est partagé par plusieurs entrées : on ajoute le nom
    return (e["id"], norm(e["nom"])) if not e["id"] else (e["id"],)

def main():
    data = json.load(open("phd_data.json", encoding="utf-8"))
    raw = fetch()
    if len(raw) < MIN_RECORDS:
        sys.exit(f"Abandon : seulement {len(raw)} enregistrements (minimum {MIN_RECORDS}).")
    sample = raw[0]
    for k in ("id", "nom", "type_ev"):
        if pick(sample, k) is None:
            sys.exit(f"Abandon : champ '{k}' introuvable. Champs disponibles : {sorted(sample)}")
    new = {key(e): e for e in map(to_entry, raw) if e["nom"] and e["lat"] is not None}

    out, added, removed, changed, seen = [], [], [], [], set()
    for e in data:
        if e.get("source") != "auto":
            out.append(e); continue
        k = key(e); seen.add(k)
        n = new.get(k)
        if n is None:
            removed.append(e); continue
        if e["cat_phd"] in ("A", "B"):  # ex. Bois de Boulogne/Vincennes : décision éditoriale, jamais rétrogradée
            n = dict(n, cat_phd=e["cat_phd"], reason=e.get("reason", n["reason"]))
        if (n["cat_phd"], n["nom"], n["type_ev"]) != (e["cat_phd"], e["nom"], e["type_ev"]):
            changed.append((e, n))
        fields = ["nom", "arr", "type_ev", "categorie", "surface", "cloture", "lat", "lon", "cat_phd"]
        if n["cat_phd"] != e["cat_phd"]: fields.append("reason")  # on ne réécrit la justification que si la catégorie change
        merged = dict(e); merged.update({f: n[f] for f in fields})
        out.append(merged)
    manual_keys = {key(e) for e in data if e.get("source") != "auto"}
    for k, n in new.items():
        if k not in seen and k not in manual_keys:
            out.append(n); added.append(n)

    counts = {c: sum(1 for e in out if e["cat_phd"] == c) for c in "ABCF"}
    json.dump(out, open("phd_data.json", "w", encoding="utf-8"), ensure_ascii=False)
    h = open("index.html", encoding="utf-8").read()
    i = h.index("window.PHD_DATA=")
    j = h.index("];", i) + 1  # fin du tableau de données
    h = h[:i] + "window.PHD_DATA=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + h[j:]
    open("index.html", "w", encoding="utf-8").write(h)

    with open("rapport.md", "w", encoding="utf-8") as r:
        r.write(f"# Mise à jour des données\n\nTotal : {len(out)} espaces — A {counts['A']}, B {counts['B']}, C {counts['C']}, F {counts['F']}\n\n")
        r.write(f"- Ajoutés : {len(added)}\n- Retirés : {len(removed)}\n- Catégorie/nom modifiés : {len(changed)}\n\n")
        r.write("## À vérifier à la main\n\n")
        r.write("- Textes écrits en dur à actualiser si les totaux ont changé : méthodologie (« 2 562 entrées », 39 / 118 / 641 / 1 764, « ~6 % »), bandeau, vignette de partage.\n")
        r.write("- Les nouveaux espaces sont classés automatiquement C ou F : vérifier sur paris.fr s'ils sont autorisés aux chiens (B) ou s'il s'agit d'un espace canin (A).\n\n")
        for t, lst in (("Ajoutés", added), ("Retirés", removed)):
            r.write(f"### {t}\n\n")
            for e in lst[:200]: r.write(f"- {e['nom']} ({e['arr']}e, {e['type_ev']}) → {e['cat_phd']}\n")
            r.write("\n")
        r.write("### Modifiés\n\n")
        for o, n in changed[:200]: r.write(f"- {o['nom']} : {o['cat_phd']}/{o['type_ev']} → {n['cat_phd']}/{n['type_ev']}\n")
    print(open("rapport.md", encoding="utf-8").read()[:600])

if __name__ == "__main__":
    main()
