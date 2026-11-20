#!/usr/bin/env python3
"""Chasseur de tickets Expensify (ou d'un autre repo GitHub).

Tourne dans GitHub Actions toutes les 5 minutes. Lecture seule sur le repo surveillé :
il ne poste rien sur GitHub, il envoie seulement des notifications ntfy.

Trois détections :
  1. JOUABLE    : le label « Help Wanted » vient de tomber sur un ticket qui te correspond.
  2. À PRÉPARER : le label « External » vient d'être posé, pas encore Help Wanted,
                  et (par défaut) Melvin n'a pas posté de proposition.
  3. SUIVIS     : pour chaque ticket de la liste « suivis », alerte quand Help Wanted tombe,
                  quand le C+ recommande une proposition (🎀👀🎀 : ticket perdu),
                  quand le ticket est fermé ou qu'une nouvelle proposition arrive.

Tous les réglages sont dans config.json. L'état (ce qui a déjà été signalé) est dans etat.json.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"
ICI = os.path.dirname(os.path.abspath(__file__))
FICHIER_CONFIG = os.path.join(ICI, "config.json")
FICHIER_ETAT = os.path.join(ICI, "etat.json")

TOKEN = os.environ.get("GH_TOKEN", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
SUIVRE = os.environ.get("SUIVRE", "").strip()
NE_PLUS_SUIVRE = os.environ.get("NE_PLUS_SUIVRE", "").strip()

MAINTENANT = datetime.now(timezone.utc)
APPELS = {"n": 0}

RE_PROPOSITION = re.compile(r"^\s*#{1,3}\s*Proposal\b", re.IGNORECASE | re.MULTILINE)
RE_PROPOSITION_MAJ = re.compile(r"^\s*#{1,3}\s*Proposal\s*\n+\s*\[Updated\]", re.IGNORECASE)
RE_CONFIANCE = re.compile(r"\b(high|medium|low)\s+confidence\b", re.IGNORECASE)
RE_MONTANT = re.compile(r"\[\$(\d+)\]")
RE_WEB = re.compile(r"(Windows\s*:\s*Chrome|MacOS\s*:\s*(Chrome|Safari|Desktop)|\bDesktop\b|\bWeb\b)", re.IGNORECASE)


# ---------------------------------------------------------------- utilitaires

def date(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")) if iso else None


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def charger(chemin, defaut):
    try:
        with open(chemin, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return defaut


def sauver(chemin, donnees, trier=True):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2, sort_keys=trier)
        f.write("\n")


def gh(chemin, params=None):
    """GET sur l'API GitHub, avec pagination simple pour les listes (max 300 éléments)."""
    url = f"{API}{chemin}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    resultats = []
    for _ in range(3):
        requete = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "chasseur-expensify",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        })
        APPELS["n"] += 1
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            donnees = json.loads(reponse.read().decode("utf-8"))
            lien = reponse.headers.get("Link", "")
        if not isinstance(donnees, list):
            return donnees
        resultats.extend(donnees)
        suivant = re.search(r'<([^>]+)>;\s*rel="next"', lien)
        if not suivant:
            break
        url = suivant.group(1)
    return resultats


def notifier(titre, message, priorite=3, lien=None, tags="mag"):
    print(f"\n--- NOTIFICATION (priorité {priorite}) ---\n{titre}\n{message}\n{lien or ''}")
    if DRY_RUN or not NTFY_TOPIC:
        return
    entetes = {"Title": titre.encode("utf-8"), "Priority": str(priorite), "Tags": tags}
    if lien:
        entetes["Click"] = lien
    requete = urllib.request.Request(
        f"https://ntfy.sh/{urllib.parse.quote(NTFY_TOPIC)}",
        data=message.encode("utf-8"), headers=entetes, method="POST")
    try:
        urllib.request.urlopen(requete, timeout=20).read()
    except urllib.error.URLError as erreur:
        print(f"ntfy a échoué : {erreur}", file=sys.stderr)


# ---------------------------------------------------------------- analyse d'un ticket

def noms_labels(ticket):
    return [l["name"] for l in ticket.get("labels", [])]


def a_label(ticket, nom):
    return any(n.lower() == nom.lower() for n in noms_labels(ticket))


def section_plateformes(corps):
    """Renvoie le texte de la section « Platforms » du ticket (cases cochées seulement si la liste en a)."""
    m = re.search(r"Platforms?\s*:?(.*?)(\n\s*#+\s|\n\s*\*\*[A-Z]|Screenshots|Applause Internal|View all open jobs|$)",
                  corps or "", re.IGNORECASE | re.DOTALL)
    texte = m.group(1) if m else ""
    if re.search(r"-\s*\[[ xX]\]", texte):
        texte = "\n".join(l for l in texte.splitlines() if re.search(r"-\s*\[[xX]\]", l))
    return texte


def plateformes_lisibles(corps):
    texte = section_plateformes(corps)
    lignes = [re.sub(r"^[-*\s]*(\[[xX ]\])?\s*", "", l).strip() for l in texte.splitlines()]
    lignes = [l for l in lignes if l and ":" in l and len(l) < 60 and not l.lower().startswith("select")]
    return lignes


def est_web(corps):
    return bool(RE_WEB.search(section_plateformes(corps)))


def raison_exclusion(ticket, config):
    """Renvoie une raison si le ticket ne te correspond pas, sinon None."""
    titre = ticket.get("title", "")
    corps = ticket.get("body") or ""
    if "pull_request" in ticket:
        return "c'est une PR"
    for label in config["labels_exclus"]:
        if a_label(ticket, label):
            return f"label {label}"
    for debut in config["titres_exclus_debut"]:
        if titre.lower().startswith(debut.lower()) or re.sub(r"^\[\$\d+\]\s*", "", titre).lower().startswith(debut.lower()):
            return f"titre « {debut} »"
    texte = f"{titre}\n{corps}".lower()
    for mot in config["mots_exclus"]:
        if mot.lower() in texte:
            return f"mot « {mot} »"
    montant = montant_de(ticket)
    if config.get("montant_min") and montant is not None and montant < config["montant_min"]:
        return f"montant ${montant}"
    if config.get("exiger_plateforme_web") and not est_web(corps):
        return "pas reproduit sur web"
    return None


def montant_de(ticket):
    m = RE_MONTANT.search(ticket.get("title", ""))
    return int(m.group(1)) if m else None


def date_label(evenements, nom):
    """Date de la dernière pose du label (None s'il n'a jamais été posé ou a été retiré depuis)."""
    pose = None
    for e in evenements:
        if e.get("label", {}).get("name", "").lower() != nom.lower():
            continue
        if e.get("event") == "labeled":
            pose = date(e["created_at"])
        elif e.get("event") == "unlabeled":
            pose = None
    return pose


def analyser_commentaires(commentaires, depuis=None):
    """Compte les propositions, repère Melvin et la recommandation du C+ (🎀👀🎀)."""
    infos = {"propositions": 0, "propositions_avant": 0, "auteurs": [], "melvin": False,
             "melvin_confiance": None, "c_plus_recommande": False}
    for c in commentaires:
        corps = c.get("body") or ""
        auteur = (c.get("user") or {}).get("login", "")
        quand = date(c.get("created_at"))
        if "🎀" in corps and "👀" in corps:
            infos["c_plus_recommande"] = True
        if "melvin" in auteur.lower():
            confiance = RE_CONFIANCE.search(corps)
            if RE_PROPOSITION.search(corps) or confiance:
                infos["melvin"] = True
                if confiance:
                    infos["melvin_confiance"] = confiance.group(1).lower()
            continue
        if not RE_PROPOSITION.search(corps) or RE_PROPOSITION_MAJ.search(corps):
            continue
        if depuis and quand and quand < depuis:
            infos["propositions_avant"] += 1
            continue
        infos["propositions"] += 1
        if auteur not in infos["auteurs"]:
            infos["auteurs"].append(auteur)
    return infos


def fiche(repo, ticket, infos=None):
    """Résumé lisible d'un ticket pour la notification."""
    lignes = []
    plateformes = plateformes_lisibles(ticket.get("body"))
    if plateformes:
        lignes.append("Plateformes : " + ", ".join(plateformes[:6]))
    if infos is not None:
        p = infos["propositions"]
        lignes.append(f"Propositions valables : {p}" + (f" ({', '.join(infos['auteurs'][:3])})" if p else " : tu peux être le premier"))
        if infos["propositions_avant"]:
            lignes.append(f"+ {infos['propositions_avant']} postée(s) avant Help Wanted (ignorées par Expensify)")
        if infos["melvin"]:
            conf = infos["melvin_confiance"] or "non précisée"
            lignes.append(f"Melvin a proposé (confiance {conf}) : il est relu en premier")
    lignes.append(f"https://github.com/{repo}/issues/{ticket['number']}")
    return "\n".join(lignes)


# ---------------------------------------------------------------- détections

def detecter_jouables(config, etat, alertes):
    repo = config["repo"]
    limite = MAINTENANT - timedelta(hours=config["fenetre_jouable_heures"])
    tickets = gh(f"/repos/{repo}/issues", {"labels": "Help Wanted", "state": "open", "sort": "updated",
                                            "direction": "desc", "per_page": 100, "since": iso(limite)})
    print(f"[jouable] {len(tickets)} ticket(s) Help Wanted mis à jour depuis {iso(limite)}")
    for t in tickets:
        cle = f"{t['number']}:jouable"
        if cle in etat["deja_signale"]:
            continue
        raison = raison_exclusion(t, config)
        if raison:
            print(f"  #{t['number']} écarté : {raison}")
            continue
        pose = date_label(gh(f"/repos/{repo}/issues/{t['number']}/events", {"per_page": 100}), "Help Wanted")
        if not pose or pose < limite:
            continue
        infos = analyser_commentaires(gh(f"/repos/{repo}/issues/{t['number']}/comments", {"per_page": 100}), pose)
        if infos["c_plus_recommande"]:
            print(f"  #{t['number']} écarté : le C+ a déjà recommandé une proposition")
            etat["deja_signale"][cle] = iso(MAINTENANT)
            continue
        if infos["propositions"] > config["propositions_max"]:
            print(f"  #{t['number']} écarté : {infos['propositions']} propositions déjà postées")
            etat["deja_signale"][cle] = iso(MAINTENANT)
            continue
        minutes = int((MAINTENANT - pose).total_seconds() // 60)
        alertes.append({
            "priorite": 5, "tri": (0, -(montant_de(t) or 0)), "cle": cle, "numero": t["number"],
            "titre": f"🚨 JOUABLE #{t['number']} {('$' + str(montant_de(t))) if montant_de(t) else ''}".strip(),
            "message": f"{t['title']}\nHelp Wanted posé il y a {minutes} min : poste ta proposition maintenant.\n" + fiche(repo, t, infos),
            "lien": t["html_url"], "tags": "rotating_light",
        })


def detecter_a_preparer(config, etat, alertes):
    repo = config["repo"]
    limite = MAINTENANT - timedelta(hours=config["fenetre_preparer_heures"])
    tickets = gh(f"/repos/{repo}/issues", {"labels": "External", "state": "open", "sort": "updated",
                                            "direction": "desc", "per_page": 100, "since": iso(limite)})
    print(f"[préparer] {len(tickets)} ticket(s) External mis à jour depuis {iso(limite)}")
    for t in tickets:
        cle = f"{t['number']}:preparer"
        if cle in etat["deja_signale"] or a_label(t, "Help Wanted"):
            continue
        raison = raison_exclusion(t, config)
        if raison:
            print(f"  #{t['number']} écarté : {raison}")
            continue
        pose = date_label(gh(f"/repos/{repo}/issues/{t['number']}/events", {"per_page": 100}), "External")
        if not pose or pose < limite:
            continue
        infos = analyser_commentaires(gh(f"/repos/{repo}/issues/{t['number']}/comments", {"per_page": 100}))
        if infos["c_plus_recommande"]:
            etat["deja_signale"][cle] = iso(MAINTENANT)
            continue
        confiance = (infos["melvin_confiance"] or "inconnue") if infos["melvin"] else "aucune"
        if confiance not in config.get("confiances_melvin_acceptees", ["aucune", "low"]):
            print(f"  #{t['number']} écarté : Melvin a proposé (confiance {confiance}), le C+ va sûrement le garder")
            etat["deja_signale"][cle] = iso(MAINTENANT)
            continue
        if config.get("suivi_auto", True) and t["number"] not in config["suivis"]:
            config["suivis"].append(t["number"])
            print(f"  #{t['number']} ajouté automatiquement aux suivis")
        avis_melvin = "rien proposé" if not infos["melvin"] else "confiance " + (infos["melvin_confiance"] or "inconnue")
        alertes.append({
            "priorite": 4, "tri": (1, -(montant_de(t) or 0)), "cle": cle, "numero": t["number"],
            "titre": f"🛠 À PRÉPARER #{t['number']} {('$' + str(montant_de(t))) if montant_de(t) else ''}".strip(),
            "message": f"{t['title']}\nExternal posé, pas encore Help Wanted. Melvin : {avis_melvin}, donc Help Wanted a de bonnes chances de tomber. Prépare ta proposition maintenant, ne la poste pas encore.\n"
                       + fiche(repo, t, infos) + ("\nAjouté aux suivis : le guetteur de l'ordinateur sonnera dès que Help Wanted tombe." if config.get("suivi_auto", True) else ""),
            "lien": t["html_url"], "tags": "hammer_and_wrench",
        })


def photo_suivi(repo, numero):
    t = gh(f"/repos/{repo}/issues/{numero}")
    evenements = gh(f"/repos/{repo}/issues/{numero}/events", {"per_page": 100})
    pose_hw = date_label(evenements, "Help Wanted")
    infos = analyser_commentaires(gh(f"/repos/{repo}/issues/{numero}/comments", {"per_page": 100}), pose_hw)
    return t, infos, {
        "help_wanted": bool(pose_hw),
        "ferme": t.get("state") == "closed",
        "recommande": infos["c_plus_recommande"],
        "propositions": infos["propositions"],
        "assignes": sorted(a["login"] for a in t.get("assignees", [])),
    }


def surveiller_suivis(config, etat, alertes):
    repo = config["repo"]
    for numero in list(config.get("suivis", [])):
        t, infos, photo = photo_suivi(repo, numero)
        avant = etat["suivis"].get(str(numero))
        etat["suivis"][str(numero)] = photo
        print(f"[suivi] #{numero} : {photo}")
        if avant is None:
            continue  # premier passage : on note l'état sans alerter
        lien = t["html_url"]
        if photo["help_wanted"] and not avant["help_wanted"]:
            alertes.append({"priorite": 5, "tri": (0, 0), "cle": None, "numero": numero, "tags": "rotating_light",
                            "titre": f"🚨 HELP WANTED sur #{numero}", "lien": lien,
                            "message": f"{t['title']}\nPoste ta proposition maintenant.\n" + fiche(repo, t, infos)})
        if photo["recommande"] and not avant["recommande"]:
            alertes.append({"priorite": 4, "tri": (0, 0), "cle": None, "numero": numero, "tags": "x",
                            "titre": f"❌ #{numero} perdu", "lien": lien,
                            "message": f"{t['title']}\nLe C+ a recommandé une proposition (🎀👀🎀). Passe au suivant."})
        if config.get("retrait_auto", True) and (photo["recommande"] or photo["ferme"]):
            config["suivis"].remove(numero)
            etat["suivis"].pop(str(numero), None)
            print(f"  #{numero} retiré automatiquement des suivis")
        if photo["ferme"] and not avant["ferme"]:
            alertes.append({"priorite": 3, "tri": (2, 0), "cle": None, "numero": numero, "tags": "lock",
                            "titre": f"#{numero} fermé", "lien": lien, "message": t["title"]})
        if photo["propositions"] > avant["propositions"]:
            alertes.append({"priorite": 3, "tri": (2, 0), "cle": None, "numero": numero, "tags": "speech_balloon",
                            "titre": f"Nouvelle proposition sur #{numero}", "lien": lien,
                            "message": f"{t['title']}\n{photo['propositions']} proposition(s) valable(s) maintenant.\n" + fiche(repo, t, infos)})
        nouveaux = set(photo["assignes"]) - set(avant["assignes"])
        if nouveaux:
            alertes.append({"priorite": 3, "tri": (2, 0), "cle": None, "numero": numero, "tags": "bust_in_silhouette",
                            "titre": f"#{numero} assigné", "lien": lien,
                            "message": f"{t['title']}\nNouvel assigné : {', '.join(sorted(nouveaux))}"})


# ---------------------------------------------------------------- programme principal

def main():
    config = charger(FICHIER_CONFIG, {})
    etat = charger(FICHIER_ETAT, {})
    etat.setdefault("deja_signale", {})
    etat.setdefault("suivis", {})
    etat_initial = json.dumps(etat, sort_keys=True)
    config_initiale = json.dumps(config, sort_keys=True)

    # Ajout / retrait d'un ticket suivi depuis « Run workflow »
    for numero in re.findall(r"\d+", SUIVRE):
        if int(numero) not in config["suivis"]:
            config["suivis"].append(int(numero))
            print(f"Ticket #{numero} ajouté aux suivis.")
    for numero in re.findall(r"\d+", NE_PLUS_SUIVRE):
        if int(numero) in config["suivis"]:
            config["suivis"].remove(int(numero))
            etat["suivis"].pop(numero, None)
            print(f"Ticket #{numero} retiré des suivis.")

    alertes = []
    detecter_jouables(config, etat, alertes)
    detecter_a_preparer(config, etat, alertes)
    surveiller_suivis(config, etat, alertes)

    # Une seule alerte par ticket et par passage : la plus importante
    alertes.sort(key=lambda a: (-a["priorite"], a["tri"]))
    vus, uniques = set(), []
    for a in alertes:
        if a["numero"] in vus:
            if a["cle"]:
                etat["deja_signale"][a["cle"]] = iso(MAINTENANT)
            continue
        vus.add(a["numero"])
        uniques.append(a)
    alertes = uniques
    for a in alertes[: config.get("alertes_max_par_passage", 5)]:
        notifier(a["titre"], a["message"], a["priorite"], a["lien"], a["tags"])
        if a["cle"]:
            etat["deja_signale"][a["cle"]] = iso(MAINTENANT)
    if len(alertes) > config.get("alertes_max_par_passage", 5):
        print(f"{len(alertes) - config['alertes_max_par_passage']} alerte(s) reportée(s) au prochain passage.")

    # On oublie les signalements de plus de 30 jours pour garder etat.json petit
    oubli = MAINTENANT - timedelta(days=30)
    etat["deja_signale"] = {k: v for k, v in etat["deja_signale"].items() if date(v) >= oubli}

    print(f"\n{len(alertes)} alerte(s), {APPELS['n']} appel(s) à l'API GitHub.")
    if DRY_RUN:
        print("Mode essai : rien n'a été envoyé ni enregistré.")
        return
    if json.dumps(etat, sort_keys=True) != etat_initial:
        sauver(FICHIER_ETAT, etat)
    if json.dumps(config, sort_keys=True) != config_initiale:
        sauver(FICHIER_CONFIG, config, trier=False)


if __name__ == "__main__":
    main()
