# clockify-migrate

Upload des exports CSV Clockify (rapport *Detailed*) vers un autre compte Clockify via l'API.
Marche aussi sur le plan Free, où l'import de timesheets est grisé dans l'interface.

## 1. Environnement conda

```bash
conda env create -f environment.yml
conda activate clockify-migrate
```

Après une modification de `environment.yml` : `conda env update -f environment.yml --prune`.

## 2. Clé API (nouveau compte)

1. Connecte-toi à clockify.me avec **julien.dupont.rochat@gmail.com**
2. Clique sur ta photo de profil → **Profile settings**
3. Onglet **Advanced** → **Manage API keys** → **Generate new**
4. Donne-lui un nom (ex. `migration`), clique **Generate**, puis **copie la clé immédiatement** :
   elle n'est plus visible ensuite.
5. Colle-la dans `config.toml` :

```toml
[clockify]
api_key = "ta-clé-ici"
```

`config.toml` est dans `.gitignore`. Le modèle versionné est `config.example.toml`.
La variable d'environnement `CLOCKIFY_API_KEY` remplace la clé du fichier si elle est définie.

Vérifie la connexion :

```bash
clockify-migrate check
```

La commande affiche l'e-mail du compte (ça doit être le gmail) et ses workspaces. Pour cibler un autre
workspace que le workspace actif, copie son ID dans `workspace_id`.

## 3. Upload

Mets tes exports dans `csv/`, puis :

```bash
clockify-migrate upload csv/ --dry-run   # simulation : lit tout, n'envoie rien
clockify-migrate upload csv/             # import réel
```

On peut aussi passer un ou plusieurs fichiers :

```bash
clockify-migrate upload csv/Clockify_Time_Report_Detailed_2023.csv
clockify-migrate upload examples/exemple_clockify.csv --dry-run
```

Le script :
- crée les clients, projets, tâches et tags manquants (et réutilise ceux qui existent) ;
- crée les entrées de temps en convertissant l'heure locale (`timezone`) en UTC ;
- ne prend que les lignes de `old_email` ;
- enregistre chaque entrée envoyée dans `imported.log` → on peut relancer sans doublons ;
- écrit les lignes en échec dans `errors.csv` et continue.

## 4. Compter les heures

```bash
clockify-migrate hours                              # depuis le compte Clockify, par mois
clockify-migrate hours csv/                         # depuis les exports CSV
clockify-migrate hours --par total
clockify-migrate hours --par annee
clockify-migrate hours --par mois --du 2026-01-01 --au 2026-06-30
clockify-migrate hours --par semaine --du 01.09.2026 --au 30.09.2026
clockify-migrate hours csv/ --par projet --du 2025-01-01
```

- `--par` : `total`, `annee`, `mois` (défaut), `semaine`, `jour`, `projet`
- `--du` / `--au` : dates incluses, format `2026-12-31`, `31.12.2026` ou `31/12/2026`
- Sans chemin, les heures viennent du compte configuré ; avec un chemin, des CSV
  (filtrés sur `old_email`, doublons entre exports comptés une seule fois).
  Comparer les deux permet de vérifier que l'import est complet.

## 5. Trouver les entrées manquantes

```bash
clockify-migrate compare csv/
clockify-migrate compare csv/ --du 2026-01-01
```

Liste les entrées des CSV absentes du compte (et celles du compte absentes des CSV),
comparées sur la date et les heures de début et de fin.

Pour envoyer ce qui manque sans se fier à `imported.log` (par exemple après avoir supprimé
des entrées à la main dans Clockify) :

```bash
clockify-migrate upload csv/ --resync
```

## Configuration (`config.toml`)

| Clé | Rôle |
|---|---|
| `clockify.api_key` | clé API du compte cible |
| `clockify.workspace_id` | vide = workspace actif |
| `import.timezone` | fuseau des heures dans les CSV (`Europe/Zurich`) |
| `import.old_email` | filtre sur la colonne Email (vide = tout) |
| `import.date_order` | `DMY` (31/12/2026), `MDY`, `YMD` ou `auto` |
| `import.send_billable` | `false` si l'API refuse le flag facturable |
| `import.default_project` | projet attribué aux lignes sans projet (si le workspace exige un projet) |
| `import.state_file` | fichier anti-doublons |

## Tout recommencer

Supprime les entrées dans Clockify, puis efface `imported.log`.
