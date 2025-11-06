# jellyfin-manager — Utilitaires d'audit et gestion des accès aux bibliothèques Jellyfin

Ce dépôt contient un script utilitaire `check_users_library.py` pour auditer et modifier l'accès des utilisateurs aux bibliothèques (virtual folders) d'un serveur Jellyfin.

## Fonctionnalités principales

- Lister les bibliothèques (ID -> Nom).
- Auditer quels utilisateurs ont accès à quelles bibliothèques (export CSV possible).
- Ajouter une bibliothèque à tous les utilisateurs (`--add-library`).
- Retirer une bibliothèque de tous les utilisateurs (`--del-library`).
- Dry-run par défaut ; utilisez `--apply` pour effectuer les modifications.

## Prérequis

- Python 3.8+
- Package Python : `requests`

Vous pouvez installer la dépendance minimale :

```bash
pip install -r requirements.txt
```

(ou `pip install requests` si vous préférez)

## Fichier principal

- `check_users_library.py` : script CLI

## Usage

Les options principales du script :

- `--url` : URL du serveur Jellyfin (ex: `http://10.0.0.2:8096` ou `https://jellyfin.example`)
- `--api-key` : clé API (en-tête `X-Emby-Token`)
- `--list` : liste toutes les bibliothèques connues (ID -> Nom) et quitte
- `--add-library <ID|NAME>` : ajoute la bibliothèque (ID ou nom) à tous les utilisateurs (dry-run si `--apply` absent)
- `--del-library <ID|NAME>` : retire la bibliothèque (ID ou nom) de tous les utilisateurs (dry-run si `--apply` absent)
- `--apply` : applique réellement les modifications (sinon dry-run)
- `--insecure` : désactive la vérification TLS (HTTPS)
- `-o, --output` : chemin CSV d'export pour l'audit
- `--timeout <s>` : timeout réseau

### Lister les bibliothèques

```bash
python3 ./check_users_library.py --url https://jellyfin.example --api-key YOUR_API_KEY --list
```

Sortie attendue : une liste `ID -> Nom` triée par nom.

### Ajouter une bibliothèque à tous les utilisateurs (dry-run)

```bash
python3 ./check_users_library.py --url https://jellyfin.example --api-key YOUR_API_KEY --add-library 'Movies Externe'
```

Si vous voulez appliquer réellement les modifications :

```bash
python3 ./check_users_library.py --url https://jellyfin.example --api-key YOUR_API_KEY --add-library 'Movies Externe' --apply
```

Le script résout l'argument de bibliothèque de plusieurs façons :
- si c'est un ID exact connu, il l'utilise
- sinon recherche un nom exact (insensible à la casse)
- sinon recherche par "contains" (si une seule correspondance)
- si plusieurs correspondances, il demande d'utiliser l'ID exact

### Retirer une bibliothèque de tous les utilisateurs (dry-run)

```bash
python3 ./check_users_library.py --url https://jellyfin.example --api-key YOUR_API_KEY --del-library 'Movies Externe'
```

Appliquer la suppression :

```bash
python3 ./check_users_library.py --url https://jellyfin.example --api-key YOUR_API_KEY --del-library 'Movies Externe' --apply
```

### Notes sur les permissions et méthodes HTTP

- Le script utilise l'API Jellyfin (endpoints type `/Library/VirtualFolders`, `/Users`, `/Users/{id}/Policy`, `/Items`).
- Pour modifier la policy utilisateur, le script tente d'abord une requête `PUT /Users/{id}/Policy`. Si le serveur répond `405 Method Not Allowed`, il retente en `POST` (certains déploiements Jellyfin/versions attendent POST plutôt que PUT).
- La clé API utilisée doit avoir les droits nécessaires pour lire les utilisateurs/bibliothèques et modifier les policies utilisateurs.

### Export CSV

Utilisez `-o audit.csv` pour écrire un rapport des accès par utilisateur.

```bash
python3 ./check_users_library.py --url https://jellyfin.example --api-key YOUR_API_KEY -o audit.csv
```

### Conseils de sécurité

- Ne stockez pas la clé API en clair dans des dépôts publics.
- Si vous utilisez `--insecure`, vous désactivez la vérification TLS — n'utilisez cela que pour des tests sur des environnements contrôlés.

## Dépannage

- Si `--list` ne renvoie rien, vérifiez que la clé API a accès à `/Library/VirtualFolders` ou `/Library/MediaFolders`.
- En cas d'erreurs 403/401 : vérifiez la clé API et les permissions côté Jellyfin.
- En cas d'erreurs 405 lors du `PUT` : le script retente automatiquement en `POST` si possible.

## Contribuer

Ouvert aux améliorations : tests unitaires, extraction de la résolution d'ID dans une fonction réutilisable, couverture des cas d'API non standard.

---

Fichier modifié : `check_users_library.py` — voir les nouveaux flags `--list`, `--add-library`, `--del-library` et le fallback PUT->POST.
