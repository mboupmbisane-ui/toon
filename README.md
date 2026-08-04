# Synchronisation Google Earth Engine → THIAM ECOLOGIQUE

Calcule de vraies données de déforestation (Hansen Global Forest Change) et de
NDVI Sentinel-2 pour chaque forêt suivie, et les dépose dans Supabase pour
que la plateforme les affiche.

## Mise en place (une seule fois)

1. **Projet Google Cloud + Earth Engine**
   - Créer/choisir un projet sur https://console.cloud.google.com
   - L'enregistrer pour Earth Engine : https://developers.google.com/earth-engine/guides/access

2. **Compte de service**
   - Dans IAM & Admin → Comptes de service → Créer, rôle "Earth Engine Resource Viewer"
   - Générer une clé JSON (Actions → Gérer les clés → Créer une clé → JSON)
   - Guide complet : https://developers.google.com/earth-engine/guides/service_account

3. **Policy Supabase (une seule fois, SQL Editor)**
   ```sql
   create policy "gee_reel public read" on app_kv for select
     using (key like 'gee\_reel\_%' escape '\');
   ```

4. **Secrets GitHub** (repo → Settings → Secrets and variables → Actions) :
   - `GEE_SERVICE_ACCOUNT_JSON` — contenu complet du fichier JSON de la clé
   - `GEE_PROJECT` — l'ID du projet Google Cloud
   - `SUPABASE_URL` — ex. `https://jbjsxhztdwxpvnahlhje.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` — clé **service_role** (Settings → API dans
     Supabase). ⚠️ Jamais dans le HTML, jamais publique — seulement en secret
     GitHub Actions.

5. Pousser ce dossier + `.github/workflows/gee-sync.yml` dans votre dépôt.
   Le workflow tourne automatiquement tous les jours à 6h UTC, ou peut être
   lancé manuellement depuis l'onglet **Actions** → "Run workflow".

## Test local

```bash
pip install -r requirements.txt
export GEE_SERVICE_ACCOUNT_JSON="$(cat ma-cle.json)"
export GEE_PROJECT="mon-projet"
export SUPABASE_URL="https://jbjsxhztdwxpvnahlhje.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="..."
python sync_gee.py
```

## Ajouter une forêt

Éditez `forests.json` (id, nom, lat, lon, superficie) — gardez-le synchronisé
avec le tableau `FORESTS` de `index.html`.
