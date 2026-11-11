# Prédiction de charge d'un conteneur Docker

Prototype expérimental qui collecte des métriques d'application et de conteneur, prévoit la charge à 60 secondes avec Random Forest et affiche les résultats dans Grafana.

## Architecture

```text
Application FastAPI ── métriques applicatives ──┐
        │                                        ├── Prometheus ── Grafana
      Docker ── cAdvisor ── métriques réelles ───┤
                                                 └── Prédicteur Random Forest ── métriques de prévision
```

L'application de démonstration garde des métriques `app_*` simulées pour contrôler les scénarios de charge. Le service Random Forest ne les utilise plus : il interroge les mesures cAdvisor réelles associées à `name="charge-demo-app"` dans Prometheus. La CPU est calculée avec `rate(container_cpu_usage_seconds_total[1m]) * 100`; la RAM utilise `container_memory_working_set_bytes / 1024 / 1024`.

## Démarrage

Prérequis : Docker Desktop avec Docker Compose, et un environnement Linux ou Docker Desktop compatible avec cAdvisor.

Depuis ce dossier :

```bash
docker compose up --build -d
```

Accès :

- Application et API : http://localhost:8000
- Prometheus : http://localhost:9090
- Grafana : http://localhost:3000 (identifiants de démonstration : `admin` / `admin`)
- cAdvisor : http://localhost:8080
- Métriques du prédicteur : http://localhost:9100/metrics

## Créer / enrichir le dataset CSV

Le collecteur `collection/export_dataset.py` récupère depuis Prometheus les mesures cAdvisor réelles de CPU et RAM, les synchronise par horodatage UTC et les ajoute à `data/raw/container_metrics.csv`. Il utilise uniquement la bibliothèque standard Python.

Depuis la racine `outputs`, exporter la dernière heure :

```powershell
py collection/export_dataset.py --hours 1 --scenario historique_non_etiquete
```

Pour générer automatiquement un dataset étiqueté, lancez depuis le dossier `outputs` :

```powershell
py collection/export_dataset.py --scenarios --stage-seconds 180
```

Le script pilote `POST /load`, attend chaque palier, puis récupère les mesures CPU/RAM de cette période depuis Prometheus. La séquence comprend charge faible (0,15), moyenne (0,45), forte (0,85), montée progressive (0,30 → 0,60 → 0,90) et pics (0,20 → 0,90 → 0,20). Chaque palier dure 180 secondes par défaut; cela donne environ 12 minutes 40 secondes et environ 50 points par palier au pas de 15 secondes. Modifiez `--stage-seconds` (minimum 60 s) pour changer la durée. À la fin, le niveau de charge est remis à 0,35 par défaut; utilisez `--restore-level` pour choisir une autre valeur.

Conditions : Docker Compose doit déjà être démarré, et l'application doit répondre sur `http://localhost:8000` et Prometheus sur `http://localhost:9090`. Pour empêcher le mélange avec l'ancien historique, choisissez un nouveau fichier, par exemple `--output data/raw/scenarios_2026.csv`. Le CSV principal existant reste conservé si vous utilisez son chemin par défaut. Les appels répétés ne dupliquent pas les mêmes lignes pour le même horodatage, conteneur et scénario. Gardez les données historiques non étiquetées à part lors de l'évaluation.

### Collecte depuis plusieurs PC et GitHub

Chaque PC collecte d'abord son propre fichier, car GitHub ne permet pas à plusieurs machines d'écrire en direct dans un CSV commun. Sur chaque PC, clonez le dépôt, démarrez Docker Compose et lancez par exemple `py collection/export_dataset.py --scenarios --stage-seconds 180 --output data/raw/collecte_NOM.csv` (remplacez NOM par un identifiant simple). Envoyez ensuite le fichier au responsable ou créez une branche et une Pull Request. Le responsable fusionne les fichiers reçus avec : `py collection/merge_datasets.py --output data/raw/dataset_partage.csv data/raw/collecte_*.csv`. L'outil contrôle les colonnes attendues et déduplique par horodatage, conteneur et scénario. Les fichiers `collecte_*.csv` restent ignorés par Git par défaut; le dataset fusionné peut être ajouté explicitement après vérification avec `git add -f data/raw/dataset_partage.csv`.

Pour publier le code : créez un dépôt GitHub vide, puis depuis ce dossier exécutez `git init`, `git add .`, `git commit -m "Initial project"`, `git branch -M main`, `git remote add origin <URL_DU_DEPOT>` et `git push -u origin main`. Ne publiez pas de secrets ni de fichiers CSV contenant des données privées.

Colonnes : `timestamp_utc`, `container`, `cpu_percent`, `ram_mb`, `scenario`. La CPU est l'utilisation exprimée en pourcentage d'un cœur; elle peut dépasser 100 % si un conteneur consomme plusieurs cœurs.

Au premier démarrage, attendez quelques minutes pour accumuler l'historique nécessaire à Random Forest. Avant cela, le service publie la dernière valeur disponible et `prediction_model_ready` vaut 0.

## Générer des variations de charge

Envoyer une charge forte :

```bash
curl -X POST http://localhost:8000/load -H "Content-Type: application/json" -d "{\"level\":0.9}"
```

Charge basse : remplacer `0.9` par `0.15`. La valeur doit rester entre 0 et 1. Interroger `GET /` pour connaître le niveau courant.

## Méthode de prédiction

Le prédicteur interroge Prometheus toutes les 15 secondes, récupère les 30 dernières minutes de métriques réelles, construit des fenêtres glissantes de 8 points et entraîne un Random Forest distinct pour CPU et mémoire. Il applique ensuite une prévision récursive jusqu'à l'horizon configuré (`FORECAST_SECONDS`, 60 s par défaut). L'historique n'est pas mélangé aléatoirement. Le modèle est réentraîné à chaque cycle sur la fenêtre récente; il s'agit d'un prototype, pas encore d'une comparaison scientifique avec train/validation/test.

Ce prototype n'est pas encore un benchmark scientifique : il faut collecter plusieurs scénarios, séparer les périodes d'entraînement et de test dans l'ordre temporel, comparer une référence naïve et d'autres modèles, et calculer MAE/RMSE par horizon.

## Limites et suites proposées

1. Enregistrer des scénarios (stable, montée, pic, périodique) avec un générateur de charge reproductible.
3. Comparer persistance, Random Forest, XGBoost et LSTM/GRU avec séparation temporelle, MAE/RMSE et temps de prédiction.
4. Ajouter des seuils/alertes de risque et enrichir le tableau de bord.
5. Étudier ensuite une version Kubernetes et comparer autoscaling réactif et prédictif en simulation contrôlée.

## Arrêt

```bash
docker compose down
```

Les volumes Prometheus et Grafana sont conservés. Pour les supprimer également : `docker compose down -v`.
