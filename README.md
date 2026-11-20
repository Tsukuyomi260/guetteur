# Chasseur de tickets

Repère les tickets GitHub sur lesquels tu peux vraiment travailler et te prévient sur ton téléphone (ntfy).
Lecture seule : il ne poste jamais rien sur le dépôt surveillé.

## Les deux pièces

| Pièce | Où elle tourne | Rôle | Délai |
|---|---|---|---|
| `chasse.py` + `.github/workflows/guetteur.yml` | GitHub Actions, toutes les 5 min | Trouve les tickets prometteurs, les ajoute aux suivis, alerte | 5 à 15 min |
| `guetteur.html` | Ton ordinateur, onglet Chrome ouvert | Surveille les tickets suivis et sonne dès que Help Wanted tombe | 10 à 65 s |

## Ce que le chasseur signale

- **🛠 À PRÉPARER** : label External posé récemment, pas encore Help Wanted, et Melvin n'a rien proposé ou a écrit « low confidence ». Sur les tickets récents, c'est presque toujours sur ceux-là que Help Wanted finit par tomber. Le ticket est ajouté automatiquement aux suivis.
- **🚨 JOUABLE** : Help Wanted vient de tomber sur un ticket qui te correspond, avec peu de propositions valables.
- **Suivis** : Help Wanted posé, nouvelle proposition, nouvel assigné, ticket fermé, ou **ticket perdu** (le C+ a posté 🎀👀🎀). Un ticket perdu ou fermé est retiré tout seul.

## Réutiliser et régler

Tout se règle dans `config.json`, sans toucher au code :

- `repo` : le dépôt surveillé (par défaut `Expensify/App`).
- `fenetre_jouable_heures`, `fenetre_preparer_heures` : jusqu'où regarder en arrière.
- `propositions_max` : au-delà, un ticket Help Wanted est jugé saturé.
- `montant_min` : ignore les tickets moins payés (ex. `250`).
- `confiances_melvin_acceptees` : `["aucune", "low"]` par défaut. Ajoute `"medium"` pour être moins strict.
- `labels_exclus`, `titres_exclus_debut`, `mots_exclus` : ce qui ne te correspond pas.
- `exiger_plateforme_web` : `true` pour ne garder que les tickets reproduits sur web ou desktop.
- `suivi_auto`, `retrait_auto` : ajout et retrait automatiques des suivis.
- `suivis` : la liste des tickets surveillés (remplie par le chasseur, modifiable à la main).

## Actions manuelles (onglet Actions, « Chasseur de tickets », Run workflow)

- **suivre** : ajoute un ou plusieurs numéros (`107444,107500`).
- **ne_plus_suivre** : en retire.
- **essai** : affiche ce que le chasseur trouverait, sans notifier ni enregistrer.
- **test_notification** : envoie juste une notification de test.

## Installation sur un autre compte

1. Copier ces fichiers dans un dépôt public (Actions gratuites).
2. Ajouter le secret `NTFY_TOPIC` (Settings, Secrets and variables, Actions).
3. Ouvrir `guetteur.html` dans Chrome sur l'ordinateur, renseigner le dépôt et le sujet ntfy, puis Démarrer.
