#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Jellyfin - Audit d'accès aux bibliothèques (virtual folders) pour tous les utilisateurs.

Fonctionnalités :
- Récupère tous les utilisateurs
- Lit la "policy" de chaque utilisateur (EnableAllFolders / EnabledFolders)
- Récupère la liste des bibliothèques et résout les noms (ID -> Name)
- Produit un tableau lisible en console + un export CSV

Usage :
  python jellyfin_audit_libraries.py --url http://<HOST>:8096 --api-key <API_KEY> [-o audit.csv]
  (HTTPS : --url https://<HOST>:8920 ; désactiver la vérification TLS : --insecure)
"""

import argparse
import csv
import sys
from typing import Dict, List, Tuple
import requests

def parse_args():
    p = argparse.ArgumentParser(description="Audit des accès aux bibliothèques Jellyfin.")
    p.add_argument("--url", required=True, help="URL de base Jellyfin (ex: http://10.0.0.2:8096)")
    p.add_argument("--api-key", required=True, help="Clé API Jellyfin (X-Emby-Token)")
    p.add_argument("-o", "--output", default="", help="Fichier CSV de sortie")
    p.add_argument("--insecure", action="store_true", help="Désactiver la vérification TLS (HTTPS)")
    p.add_argument("--timeout", type=int, default=20, help="Timeout réseau (secondes)")
    p.add_argument("--add-library", help="ID ou nom de la bibliothèque à ajouter à tous les utilisateurs (dry-run si --apply absent)")
    p.add_argument("--list", action="store_true", help="Lister toutes les bibliothèques (ID -> Nom) et quitter")
    p.add_argument("--apply", action="store_true", help="Appliquer les modifications ; si absent, le script fera un dry-run et affichera les changements prévus")
    return p.parse_args()

class JF:
    def __init__(self, base_url: str, api_key: str, verify_tls: bool, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "X-Emby-Token": api_key,
            "Accept": "application/json"
        })
        self.verify = verify_tls
        self.timeout = timeout

    def get(self, path: str, params: dict = None):
        url = f"{self.base_url}{path}"
        r = self.session.get(url, params=params or {}, verify=self.verify, timeout=self.timeout)
        r.raise_for_status()
        if r.text.strip() == "":
            return {}
        return r.json()

    def put(self, path: str, json: dict = None):
        """Effectue une requête PUT JSON et retourne le JSON réponse (ou lève)."""
        url = f"{self.base_url}{path}"
        r = self.session.put(url, json=json or {}, verify=self.verify, timeout=self.timeout)
        r.raise_for_status()
        if r.text.strip() == "":
            return {}
        return r.json()

    def post(self, path: str, json: dict = None):
        """Effectue une requête POST JSON et retourne le JSON réponse (ou lève)."""
        url = f"{self.base_url}{path}"
        r = self.session.post(url, json=json or {}, verify=self.verify, timeout=self.timeout)
        r.raise_for_status()
        if r.text.strip() == "":
            return {}
        return r.json()

def widen_mapping_with_items_api(jf: 'JF', ids: List[str], known: Dict[str, str]) -> None:
    """Complète le mapping ID->Name via /Items?Ids=... pour les IDs inconnus."""
    missing = [i for i in ids if i and i not in known]
    if not missing:
        return
    # /Items accepte une liste d'IDs séparés par des virgules
    chunksize = 50
    for i in range(0, len(missing), chunksize):
        batch = missing[i:i+chunksize]
        try:
            data = jf.get("/Items", params={"Ids": ",".join(batch)})
            # Réponse typique: {"Items":[{"Id":"...", "Name":"...", ...}, ...]}
            items = data.get("Items") if isinstance(data, dict) else None
            if isinstance(items, list):
                for it in items:
                    _id = it.get("Id")
                    name = it.get("Name")
                    if _id and name:
                        known[_id] = name
        except Exception:
            # En cas d'échec, on continue sans interrompre l'audit
            pass

def fetch_virtual_folders(jf: 'JF') -> Dict[str, str]:
    """
    Construit un mapping {ItemId -> Name} pour les bibliothèques Jellyfin.
    Sources tentées (selon la version Jellyfin) :
    - /Library/VirtualFolders
    - /Library/MediaFolders  (fallback)
    - /Library/LibraryOptions (certaines versions)
    Puis, si besoin, résolution complémentaire via /Items?Ids=...
    """
    mapping: Dict[str, str] = {}

    # 1) /Library/VirtualFolders
    try:
        vf = jf.get("/Library/VirtualFolders")
        # Quelques formes observées selon versions :
        # a) Liste de dossiers : [{"Name":"Films","ItemId":"abc123", ...}, ...]
        # b) Objet avec "Items": {"Items":[{...}]}
        containers = None
        if isinstance(vf, list):
            containers = vf
        elif isinstance(vf, dict):
            containers = vf.get("Items") or vf.get("VirtualFolders") or vf.get("ItemsList")
        if isinstance(containers, list):
            for it in containers:
                item_id = it.get("ItemId") or it.get("Id")
                name = it.get("Name")
                if item_id and name:
                    mapping[item_id] = name
                # Parfois les virtual folders contiennent "LibraryOptions"->"ItemIds"
                opts = it.get("LibraryOptions") if isinstance(it, dict) else None
                if isinstance(opts, dict):
                    item_ids = opts.get("ItemIds")
                    if isinstance(item_ids, list) and name and item_ids:
                        # Tous ces ItemIds réfèrent la même bibliothèque logique (virtual folder)
                        for iid in item_ids:
                            if iid and iid not in mapping:
                                mapping[iid] = name
    except Exception:
        pass

    # 2) /Library/MediaFolders (fallback)
    if not mapping:
        try:
            mf = jf.get("/Library/MediaFolders")
            # Formes observées :
            # {"Items":[{"Id":"...", "Name":"..."}, ...]}
            items = mf.get("Items") if isinstance(mf, dict) else None
            if isinstance(items, list):
                for it in items:
                    _id = it.get("Id")
                    name = it.get("Name")
                    if _id and name:
                        mapping[_id] = name
        except Exception:
            pass

    return mapping

def fetch_users(jf: 'JF') -> List[Tuple[str, str]]:
    """Retourne la liste des utilisateurs (Id, Name)."""
    data = jf.get("/Users")
    users = []
    if isinstance(data, list):
        for u in data:
            uid = u.get("Id")
            name = u.get("Name")
            if uid and name:
                users.append((uid, name))
    elif isinstance(data, dict) and isinstance(data.get("Items"), list):
        for u in data["Items"]:
            uid = u.get("Id")
            name = u.get("Name")
            if uid and name:
                users.append((uid, name))
    return users

def fetch_user_policy(jf: 'JF', user_id: str) -> dict:
    """Récupère la policy de l'utilisateur (endpoint dédié si possible, sinon via /Users/{id})."""
    # 1) Endpoint dédié (souvent disponible) :
    try:
        pol = jf.get(f"/Users/{user_id}/Policy")
        if isinstance(pol, dict) and pol:
            return pol
    except Exception:
        pass
    # 2) Fallback via /Users/{id}
    try:
        u = jf.get(f"/Users/{user_id}")
        if isinstance(u, dict) and isinstance(u.get("Policy"), dict):
            return u["Policy"]
    except Exception:
        pass
    return {}

def main():
    args = parse_args()
    jf = JF(args.url, args.api_key, verify_tls=not args.insecure, timeout=args.timeout)

    # --- Collecte des bibliothèques et des utilisateurs ---
    users = fetch_users(jf)
    if not users:
        print("Aucun utilisateur trouvé. Vérifiez l'URL/la clé API et les permissions.", file=sys.stderr)
        sys.exit(2)

    folder_map = fetch_virtual_folders(jf)  # {folder_id: folder_name}

    # Si on veut simplement lister les bibliothèques et quitter
    if args.list:
        if not folder_map:
            print("Aucune bibliothèque trouvée via l'API (vérifiez l'URL/la clé API).", file=sys.stderr)
            sys.exit(1)
        # Afficher trié par nom
        items = sorted(folder_map.items(), key=lambda x: (x[1] or "").lower())
        for fid, name in items:
            print(f"{fid} -> {name}")
        sys.exit(0)

    # Si on veut ajouter une bibliothèque à tous les utilisateurs
    if args.add_library:
        target = args.add_library
        # Si target est directement un ID connu
        target_id = None
        if target in folder_map:
            target_id = target
            target_name = folder_map[target_id]
        else:
            # Recherche par nom (insensible à la casse)
            matches = [fid for fid, name in folder_map.items() if isinstance(name, str) and name.lower() == target.lower()]
            if len(matches) == 1:
                target_id = matches[0]
                target_name = folder_map[target_id]
            elif len(matches) > 1:
                print(f"Plusieurs bibliothèques correspondent au nom '{target}' :", file=sys.stderr)
                for m in matches:
                    print(f"  - {m} -> {folder_map.get(m)}", file=sys.stderr)
                print("Utilisez l'ID exact pour lever l'ambiguïté.", file=sys.stderr)
                sys.exit(4)
            else:
                # Recherche approximative: contient
                contains = [fid for fid, name in folder_map.items() if isinstance(name, str) and target.lower() in name.lower()]
                if len(contains) == 1:
                    target_id = contains[0]
                    target_name = folder_map[target_id]
                elif len(contains) > 1:
                    print(f"Plusieurs bibliothèques contiennent '{target}' :", file=sys.stderr)
                    for m in contains:
                        print(f"  - {m} -> {folder_map.get(m)}", file=sys.stderr)
                    print("Utilisez l'ID exact pour lever l'ambiguïté.", file=sys.stderr)
                    sys.exit(4)
                else:
                    print(f"Bibliothèque '{target}' introuvable via l'API (IDs connus : {len(folder_map)}).", file=sys.stderr)
                    sys.exit(5)

        print(f"Target library: {target_id} -> {target_name}")

        changed = 0
        skipped_all = 0
        errors = 0
        to_update = []

        for uid, uname in users:
            policy = fetch_user_policy(jf, uid)
            enable_all = bool(policy.get("EnableAllFolders"))
            if enable_all:
                skipped_all += 1
                continue

            enabled_ids = policy.get("EnabledFolders") or policy.get("EnabledFolderIds") or []
            if not isinstance(enabled_ids, list):
                enabled_ids = []

            # Normaliser en str
            enabled_ids = [str(i) for i in enabled_ids if i]
            if target_id in enabled_ids:
                # déjà présent
                continue

            # Préparer la nouvelle policy
            new_policy = dict(policy) if isinstance(policy, dict) else {}
            # Mettre à jour les deux clés potentielles
            def ensure_list_add(d: dict, key: str, val: str):
                cur = d.get(key)
                if not isinstance(cur, list):
                    cur = []
                # éviter duplicata
                cur = [str(x) for x in cur if x]
                if val not in cur:
                    cur.append(val)
                d[key] = cur

            ensure_list_add(new_policy, "EnabledFolders", target_id)
            ensure_list_add(new_policy, "EnabledFolderIds", target_id)

            # Afficher action prévue
            print(f"User: {uname} ({uid}) -> ajouter {target_name} ({target_id})")

            if args.apply:
                try:
                    # Appel API PUT pour mettre à jour la policy
                    try:
                        jf.put(f"/Users/{uid}/Policy", json=new_policy)
                    except requests.exceptions.HTTPError as he:
                        # Certains serveurs Jellyfin n'acceptent pas PUT sur /Users/{id}/Policy
                        # et requièrent POST. Si on reçoit 405, retenter en POST.
                        status = None
                        try:
                            status = he.response.status_code
                        except Exception:
                            status = None
                        if status == 405:
                            try:
                                print(f"PUT not allowed, retrying with POST for user {uname} ({uid})")
                                jf.post(f"/Users/{uid}/Policy", json=new_policy)
                            except Exception:
                                # réémettre l'exception originale pour être interceptée par le outer except
                                raise
                        else:
                            raise
                    changed += 1
                except Exception as e:
                    print(f"Erreur mise à jour {uname} ({uid}) : {e}", file=sys.stderr)
                    errors += 1
            else:
                to_update.append((uid, uname))

        # Résumé
        if args.apply:
            print(f"\nModifications appliquées : {changed} mises à jour, {skipped_all} utilisateurs ignorés (EnableAllFolders=true), {errors} erreurs.")
        else:
            print(f"\nDry-run : {len(to_update)} utilisateurs seraient modifiés, {skipped_all} ignorés (EnableAllFolders=true). Utilisez --apply pour appliquer.")


    # --- Audit par utilisateur ---
    rows = []  # pour CSV
    unknown_ids_all = set()

    for uid, uname in users:
        policy = fetch_user_policy(jf, uid)
        enable_all = bool(policy.get("EnableAllFolders"))
        enabled_ids = policy.get("EnabledFolders") or policy.get("EnabledFolderIds") or []

        # Normalisation type liste de str
        if not isinstance(enabled_ids, list):
            enabled_ids = []

        # Si "toutes bibliothèques"
        if enable_all:
            # On prend tous les IDs connus dans folder_map
            folder_ids_for_user = list(folder_map.keys())
            mode = "ALL"
        else:
            folder_ids_for_user = list({fid for fid in enabled_ids if isinstance(fid, str) and fid})
            mode = "CUSTOM"

        # Résolution des noms
        names = []
        unknown_ids = []
        for fid in folder_ids_for_user:
            name = folder_map.get(fid)
            if name:
                names.append(name)
            else:
                unknown_ids.append(fid)

        # Tentative de résolution des IDs inconnus via /Items
        if unknown_ids:
            widen_mapping_with_items_api(jf, unknown_ids, folder_map)
            names += [folder_map[i] for i in unknown_ids if i in folder_map]
            unknown_ids_all.update([i for i in unknown_ids if i not in folder_map])

        names_sorted = sorted(set(names), key=str.lower)
        rows.append({
            "User": uname,
            "Mode": mode,
            "Libraries": ", ".join(names_sorted) if names_sorted else ("(aucune)" if not enable_all else "(indéterminé)"),
        })

    # --- Affichage console ---
    # Mise en forme simple type tableau
    col_user = max(len(r["User"]) for r in rows + [{"User": "Utilisateur"}])
    col_mode = max(len(r["Mode"]) for r in rows + [{"Mode": "Mode"}])
    col_libs = max(len(r["Libraries"]) for r in rows + [{"Libraries": "Bibliothèques"}])

    header = f'{"Utilisateur".ljust(col_user)}  {"Mode".ljust(col_mode)}  {"Bibliothèques".ljust(col_libs)}'
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda x: x["User"].lower()):
        print(f'{r["User"].ljust(col_user)}  {r["Mode"].ljust(col_mode)}  {r["Libraries"]}')

    if unknown_ids_all:
        print("\nATTENTION : des IDs de bibliothèques n’ont pas pu être résolus :", file=sys.stderr)
        for fid in sorted(unknown_ids_all):
            print(f"  - {fid}", file=sys.stderr)
        print("Suggestion : vérifier que l’API retourne correctement /Library/VirtualFolders ou /Library/MediaFolders.", file=sys.stderr)

    # --- Export CSV ---
    try:
        if args.output:
            with open(args.output, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["User", "Mode", "Libraries"])
                w.writeheader()
                for r in rows:
                    w.writerow(r)
            print(f"\nCSV écrit : {args.output}")
    except Exception as e:
        print(f"\nImpossible d’écrire le CSV ({e}).", file=sys.stderr)
        sys.exit(3)

if __name__ == "__main__":
    main()
