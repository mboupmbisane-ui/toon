#!/usr/bin/env python3
"""
sync_gee.py — Calcule de VRAIS indicateurs satellite pour chaque forêt de
THIAM ECOLOGIQUE via Google Earth Engine, et les écrit dans Supabase (table
app_kv, même table que le reste de la plateforme) pour que index.html les
affiche sans avoir à parler à Earth Engine lui-même (trop complexe/fragile
à faire depuis un navigateur ou une Edge Function Deno).

Indicateurs RÉELS calculés (pas des estimations du moteur JS) :
  - treecover2000_pct   : couverture forestière réelle en 2000 (Hansen GFC)
  - perte_ha_totale     : perte forestière cumulée réelle 2001→dernière année
                          disponible (Hansen GFC, résolution ~30m)
  - perte_ha_par_annee  : détail année par année (permet un vrai taux de
                          déboisement %/an, calculé sur des données réelles)
  - gain_ha_2000_2012   : gain forestier réel 2000-2012 (Hansen GFC)
  - ndvi_recent         : NDVI Sentinel-2 réel, médiane des 60 derniers jours

Limite honnête : la zone interrogée est un cercle centré sur les coordonnées
déclarées de la forêt (rayon dérivé de la superficie déclarée), PAS le
polygone officiel exact de la forêt classée (ce polygone officiel n'existe
pas encore dans la plateforme — voir référentiel IREF/DEFCCS à connecter
séparément). Les chiffres sont donc réels mais rattachés à une zone
approximative tant que ce polygone n'est pas importé.

Pré-requis (à faire une seule fois) :
  1. Créer/avoir un projet Google Cloud, l'enregistrer pour Earth Engine :
     https://developers.google.com/earth-engine/guides/access
  2. Créer un compte de service avec le rôle "Earth Engine Resource Viewer",
     télécharger sa clé JSON :
     https://developers.google.com/earth-engine/guides/service_account
  3. pip install earthengine-api google-auth requests
  4. Variables d'environnement à fournir à ce script :
       GEE_SERVICE_ACCOUNT_JSON   = contenu JSON de la clé de service (texte)
       GEE_PROJECT                = ID du projet Google Cloud
       SUPABASE_URL                = https://jbjsxhztdwxpvnahlhje.supabase.co
       SUPABASE_SERVICE_ROLE_KEY   = clé service_role Supabase (PAS la clé anon
                                     publique — celle-ci bypass les policies RLS,
                                     à garder secrète, jamais dans le HTML)

SQL à exécuter une seule fois dans Supabase (SQL Editor), en plus des
policies déjà en place pour app_kv (voir index.html), pour que le CLIENT
(clé anon) puisse LIRE ces résultats réels :

    create policy "gee_reel public read" on app_kv for select
      using (key like 'gee\_reel\_%' escape '\');

(L'écriture se fait uniquement par ce script, avec la clé service_role qui
bypass RLS — aucune policy d'écriture publique n'est donc nécessaire ni
souhaitable.)

Lancement :
    python sync_gee.py
(voir aussi .github/workflows/gee-sync.yml pour l'exécution automatique
quotidienne via GitHub Actions, gratuite.)
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import ee
import requests
from google.oauth2 import service_account

HANSEN_ASSET = "UMD/hansen/global_forest_change_2025_v1_13"  # màj si une version plus récente sort
NDVI_WINDOW_DAYS = 60
FORESTS_FILE = os.path.join(os.path.dirname(__file__), "forests.json")


def init_earth_engine():
    key_json = os.environ.get("GEE_SERVICE_ACCOUNT_JSON")
    project = os.environ.get("GEE_PROJECT")
    if not key_json or not project:
        sys.exit("Variables manquantes : GEE_SERVICE_ACCOUNT_JSON et/ou GEE_PROJECT.")
    info = json.loads(key_json)
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/earthengine"]
    )
    ee.Initialize(credentials, project=project)


def radius_meters_from_ha(superficie_ha):
    # Rayon d'un cercle de même surface que la superficie déclarée (approximation
    # honnête tant qu'aucun polygone officiel n'est disponible — voir docstring).
    aire_m2 = max(superficie_ha, 1) * 10000
    return math.sqrt(aire_m2 / math.pi)


def analyser_foret(foret):
    lat, lon = foret["lat"], foret["lon"]
    rayon = radius_meters_from_ha(foret.get("superficie", 100))
    region = ee.Geometry.Point([lon, lat]).buffer(rayon)

    hansen = ee.Image(HANSEN_ASSET)
    pixel_area_ha = ee.Image.pixelArea().divide(10000)

    treecover_mean = hansen.select("treecover2000").reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9
    ).get("treecover2000")

    perte_totale_ha = hansen.select("loss").multiply(pixel_area_ha).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=region, scale=30, maxPixels=1e9
    ).get("loss")

    gain_ha = hansen.select("gain").multiply(pixel_area_ha).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=region, scale=30, maxPixels=1e9
    ).get("gain")

    # Perte détaillée année par année (lossyear encode l'année en 1=2001 … 25=2025)
    perte_par_annee = {}
    for code in range(1, 26):
        annee = 2000 + code
        masque = hansen.select("lossyear").eq(code)
        perte_annee_ha = masque.multiply(pixel_area_ha).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=region, scale=30, maxPixels=1e9
        ).get("lossyear")
        perte_par_annee[str(annee)] = perte_annee_ha

    # NDVI Sentinel-2 réel, composite médian des NDVI_WINDOW_DAYS derniers jours
    fin = datetime.now(timezone.utc)
    debut = fin - timedelta(days=NDVI_WINDOW_DAYS)
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(debut.strftime("%Y-%m-%d"), fin.strftime("%Y-%m-%d"))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
    )
    ndvi_img = s2.median().normalizedDifference(["B8", "B4"]).rename("ndvi")
    ndvi_mean = ndvi_img.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=10, maxPixels=1e9
    ).get("ndvi")
    n_images = s2.size()

    # Un seul appel réseau .getInfo() pour tout le dictionnaire (limite les
    # allers-retours ; c'est la bonne pratique Earth Engine)
    resultat = ee.Dictionary({
        "treecover2000_pct": treecover_mean,
        "perte_ha_totale": perte_totale_ha,
        "gain_ha_2000_2012": gain_ha,
        "perte_ha_par_annee": ee.Dictionary(perte_par_annee),
        "ndvi_recent": ndvi_mean,
        "ndvi_nb_images": n_images,
    }).getInfo()

    resultat["rayon_zone_m"] = round(rayon)
    resultat["source"] = f"Hansen Global Forest Change ({HANSEN_ASSET}) + Sentinel-2 SR (Copernicus/GEE), réel"
    resultat["calcule_le"] = datetime.now(timezone.utc).isoformat()
    return resultat


def upsert_supabase(key, value_dict):
    url = os.environ["SUPABASE_URL"].rstrip("/")
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    resp = requests.post(
        f"{url}/rest/v1/app_kv",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        json={"key": key, "value": json.dumps(value_dict), "updated_at": datetime.now(timezone.utc).isoformat()},
        timeout=30,
    )
    resp.raise_for_status()


def main():
    init_earth_engine()
    with open(FORESTS_FILE, encoding="utf-8") as f:
        forests = json.load(f)

    for foret in forests:
        print(f"→ {foret['nom']} ({foret['id']})…")
        try:
            resultat = analyser_foret(foret)
            upsert_supabase(f"gee_reel_{foret['id']}", resultat)
            print(f"  OK — perte totale {resultat['perte_ha_totale']:.1f} ha, "
                  f"couverture 2000 {resultat['treecover2000_pct']:.1f}%, "
                  f"NDVI récent {resultat['ndvi_recent']}")
        except Exception as e:
            print(f"  ERREUR pour {foret['id']} : {e}", file=sys.stderr)

    print("Terminé.")


if __name__ == "__main__":
    main()
