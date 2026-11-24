# ComfyUI-H3-ClipPlan (v3.11)

*Version anglaise : [README.md](README.md).*

> **Avertissement : ce projet est entièrement « vibe codé ».** Tout son code, sa documentation,
> ses workflows et ses exemples ont été écrits par un assistant IA (Claude, d'Anthropic), à la
> demande de l'auteur. L'auteur a défini les fonctionnalités et testé le résultat à l'usage, mais
> n'a ni écrit ni relu le code, à aucun moment. Lisez-le, testez-le sur votre installation et
> utilisez-le à vos risques ; les signalements et corrections sont les bienvenus.

Nœuds ComfyUI pour générer une vidéo MiniMax H3 à partir d'un **fichier de plan JSON**,
un clip par exécution, avec un graphe d'une trentaine de nœuds au lieu de deux cents.

Deux modes, choisis dans le plan pour tout le projet :

- **`music_video`** : vous fournissez la chanson, elle est injectée dans chaque clip et reste
  intacte ; H3 ne génère que l'image, synchronisée sur la chanson.
- **`free`** : pas de chanson ; H3 génère l'image et le son ensemble (dialogues, ambiance), avec
  la voix de chaque personnage si vous en fournissez un échantillon.

Le plan dit, pour chaque clip : sa longueur, s'il continue le plan précédent, les références
qu'il utilise et son prompt. Il ne contient rien qui dépende de la machine : les images de
référence et la résolution se choisissent dans le workflow.

**État.** La logique du plan (lecture, validation, calcul des temps, placeholders) est couverte
par des tests. La génération clip par clip a tourné dans ComfyUI avec MiniMax H3. Certaines
fonctions plus récentes ont d'abord été vérifiées en simulation (nœuds appelés avec des modèles
factices).

L'interface des nœuds et les workflows sont en anglais ; les libellés sont cités tels quels.

## Démarrage rapide avec les exemples

Le dossier `examples/` contient deux petits projets prêts à lancer, avec leurs images (dessinées
par programme) et leur chanson (synthétisée), distribuées sous la même licence que le plugin :

| Plan | Mode | Fichiers | Durée |
|---|---|---|---|
| `robot_beach.plan.json` | `music_video`, 3 clips | `robot.png`, `beach.png`, `robot_beach_song.mp3` | 19,3 s |
| `robot_workshop.plan.json` | `free`, 2 clips avec dialogue | `robot.png`, `workshop.png` (échantillon de voix `robot` facultatif) | 11,5 s |

1. Copier tous les fichiers de `examples/` dans `ComfyUI/input/`.
2. Ouvrir `workflows/H3 Clip Plan - 1 Generate.json` (ou `… Generate (free).json`) : plan,
   images et chanson sont déjà sélectionnés. Vérifier les modèles, régler le nombre
   d'exécutions sur le nombre de clips (3 ou 2) et lancer.
3. Ouvrir `workflows/H3 Clip Plan - 2 Assemble.json` (ou `… Assemble (free).json`) et le lancer
   pour obtenir la vidéo finale.

## Version 3.11 : retour à la vidéo de référence de la 3.9

- La 3.10 mettait les images de la prise précédente au début de la vidéo de référence d'un clip d'édition qui
  en continue un autre. L'image restait plus nette avec un upscale latent, mais H3 suivait alors le changement
  qu'il voyait dans la vidéo de référence (une robe rouge au début, celle d'origine ensuite) : l'édition ne
  tenait plus d'un clip à l'autre. La vidéo de référence est de nouveau la vidéo d'origine sur tout le clip,
  comme en 3.9. Pour la meilleure qualité en édition, générer sans upscale latent.

## Version 3.10 : des continuations plus nettes dans une vidéo éditée

- Un clip d'édition qui continue le précédent recevait deux images différentes de son recouvrement (les
  1,6 premières secondes) : le clip précédent, déjà édité, en contexte protégé, et la vidéo d'origine
  comme `<Video 1>`. H3 tranchait par une image plus douce, dont le clip suivant héritait : l'image perdait
  en netteté clip après clip (bien plus avec un upscale latent). Le recouvrement de sa vidéo de référence
  montre maintenant les images de la prise précédente, pour que les deux disent la même chose. Elles
  viennent de l'aperçu vidéo de la prise (ou, sans aperçu, de son latent décodé). Les prises faites avant
  sont périmées.

## Version 3.9 : consigne d'édition commune, continuer après le segment édité

- Les plans passent en `"version": 6`. Les versions 1 à 5 sont toujours lues.
- `edit_video` reçoit `"instruction"` : l'édition commune aux clips, écrite une fois. Elle remplace
  `{edit}` dans les prompts ; les images qu'elle cite (`{picture:NOM}`) sont ajoutées aux références des
  clips qui utilisent `{edit}`.
- Les clips qui finissent après la fin du segment ne sont plus une erreur : ils continuent la vidéo (pas
  de `<Video 1>`, pas de morceau de vidéo). Avec `"sound": "original"`, la vidéo finale garde le son
  d'origine sur la partie éditée seulement.

## Version 3.8 : éditer une vidéo existante

- Les plans passent en `"version": 5`. Les versions 1 à 4 sont toujours lues.
- Nouvelle clé `edit_video` (mode `free`) : un segment d'un fichier vidéo, donné par le nœud
  **H3 Plan - Video**, est édité. La frise du plan est le segment ; chaque clip reçoit son propre
  morceau comme `<Video 1>` (la vidéo de référence de H3, par « MiniMax H3 Reference to Video »), avec
  son son, que H3 numérote `<Audio 1>` : les voix sont numérotées à partir de `<Audio 2>`
  automatiquement. Le prompt dit ce qui change (style, personnage remplacé par une image de
  référence…).
- `"sound"` : la vidéo finale garde le son d'origine du segment (`"original"`, par défaut) ou le son
  fait par H3 (`"h3"`). Pour le son d'origine, « H3 Plan - Assemble Final Video » a besoin de son
  entrée `references` (le nœud vidéo).
- Un clip qui édite une vidéo dure de 2 à 15 s. `start_video` et `edit_video` ne vont pas ensemble.
- Changer le segment ou le fichier vidéo rend toutes les prises périmées.

## Version 3.7 : continuer une vidéo existante

- Les plans passent en `"version": 4`. Les versions 1 à 3 sont toujours lues.
- Nouvelle clé `start_video` : le clip 1 continue (`"continue": true`) ou coupe depuis un segment
  d'un fichier vidéo, donné par un nouveau nœud **H3 Plan - Video** du même nom. Seul le segment est
  lu (ffmpeg, celui apporté par ComfyUI-VideoHelperSuite), à 24 images/s, agrandi pour couvrir la
  taille d'image et rogné au centre. Le segment est aimanté sur la grille des images : il garde sa
  fin, son début avance de moins de 0,7 s.
- `"in_assembly"` : le segment ouvre la vidéo finale, ou ne sert que de tremplin (la vidéo commence
  alors là où commence le clip 1).
- Mode `music_video`, `"song"` : `"with_video"` (par défaut) la chanson commence avec le segment,
  qui perd son propre son ; `"after_video"` le segment garde son son et la chanson commence avec le
  clip 1.
- « H3 Plan - Assemble Final Video » a de nouvelles entrées facultatives `references`, `width`,
  `height` pour le segment ; il lui faut aussi `audio_vae` quand le segment est dans la vidéo.
- Changer le segment ou le fichier vidéo rend périmées les prises du clip 1 (et celles qui les
  continuent).

## Version 3.6 : appli intégrée retirée

- L'éditeur de plan et l'appli de génération servis par les versions 3.4 et 3.5 à
  `/h3clipplan/app` sont retirés, avec leurs adresses serveur (`/h3clipplan/settings`,
  `/h3clipplan/digest`). Les nœuds, le nœud « H3 Plan - Takes » et les adresses de prises
  utilisées par des outils externes (`takes`, `select`, `prune`, `selection`, `delete_takes`,
  `ping`) ne changent pas.
- Les entrées facultatives `take_token`, `parent_token` et `seed_override` de « H3 Plan - Clip »
  restent disponibles pour une appli externe qui pilote ComfyUI par son API.
- L'interface du nœud de prises et les workflows sont en anglais ; les workflows du mode libre
  sont renommés `… (free).json`.
- Nouveaux exemples : `robot_beach` (clip musical) et `robot_workshop` (mode libre), avec leurs
  images et leur chanson.

## Version 3.3 : les prises

Chaque génération d'un clip est conservée comme une **prise** : son latent, sa signature et
son aperçu.

```text
output/h3_clip_plan/<projet>/latents/clip_003/s<graine>_<id>.pt     latent de la prise
output/h3_clip_plan/<projet>/latents/clip_003/s<graine>_<id>.json   signature
output/h3_clip_plan/<projet>/clips/clip_003_<nom>_s<graine>_<id>_00001.mp4   aperçu
output/h3_clip_plan/<projet>/selection.json                         prise choisie pour chaque clip
```

- **Plusieurs prises** : sur « H3 Plan - Clip », `seed_mode` à `random` donne une nouvelle
  graine à chaque exécution. Lancer trois fois le même clip (contrôle de `clip_index` sur
  `fixed`) donne trois prises à comparer. `plan` utilise la graine du plan.
- **La prise la plus récente devient la prise choisie** de son clip.
- **Choisir** : le nœud « H3 Plan - Takes » (workflows d'assemblage) liste les prises de chaque
  clip. Un clic joue l'aperçu, « use this take » la choisit. Le choix est enregistré dans
  le projet (`selection.json`), pas dans le workflow.
- **Continuations** : une prise d'un clip `continue` est construite sur une prise précise du
  clip précédent. Elle n'est utilisable que si cette prise-là est choisie (sinon elle apparaît
  en pointillés). Choisir une telle prise choisit aussi celle du clip précédent. Un clip
  `continue` généré ensuite part de la prise choisie du clip précédent.
- **Prises périmées** : plan modifié (la graine mise à part), autre image ou voix que la prise
  la plus récente, autre résolution, ou prise du clip précédent disparue. Avec `delete_stale`
  (activé par défaut), elles sont supprimées avec leurs aperçus après chaque génération. Les
  latents des versions précédentes (sans prise) sont supprimés aussi.
- **Assemblage** : il prend la prise choisie de chaque clip et s'arrête avant le premier clip
  sans prise utilisable. Son rapport donne les prises utilisées.
- **Ménage** : « delete unchosen takes » dans le nœud de prises efface toutes les
  prises non choisies et leurs aperçus. Les vidéos finales ne sont jamais supprimées.
- Une prise pèse environ 15 Mo (clip de 10 s en 960 × 544).

Attention : changer l'image ou la voix d'une référence rend périmées toutes les prises qui
l'utilisent, même si vous ne refaites qu'un clip. Pour un essai isolé, désactivez `delete_stale`.

## Version 3.1

- Correction du nœud « H3 Plan - Voice » : son champ de fichier restait bloqué (« UNKNOWN »).
  Il devient une liste simple des fichiers audio de `ComfyUI/input` (sous-dossiers compris),
  avec un bouton `choose audio file` et le glisser-déposer d'un fichier sur le nœud. Le fichier
  envoyé est copié dans `ComfyUI/input/voices/`.
- Les nœuds VOIX d'un workflow 3.0 sont à supprimer et à recréer (ou rouvrir le workflow
  « 1 Generate (free) »).

## Nouveautés de la version 3

- **Mode `free`** : H3 génère le son de chaque clip. Les continuations reprennent la fin du clip
  précédent en image **et en son** (1,6 s copiée et protégée, relâchée en douceur sur 0,2 s), au
  moyen du nœud `MiniMaxH3GeneratedAVMaskedContext` du pack MultiRef.
- **Nœud « H3 Plan - Voice »** : un échantillon de voix (2 à 15 s) avec le même nom que l'image
  du personnage, ou un nom à lui pour un personnage hors champ. Sans nœud de voix, H3 choisit la
  voix.
- **Placeholders dans les prompts** : `{picture:nom}` et `{voice:nom}`. Le nœud les numérote
  (`<Picture N>`, `<Audio N>`) selon ce que le workflow fournit vraiment ; une ligne
  `{voice:nom}` sans voix fournie est retirée. Les prompts à `<Picture N>` des anciens plans
  marchent toujours.
- **Assemblage du mode `free`** : le son de chaque clip est décodé et recollé aux mêmes endroits
  que l'image.
- **Champ `dialogue`** par clip : le vérificateur contrôle les locuteurs, la présence du texte
  dans le prompt et le débit (au plus 3 mots par seconde).
- Deux workflows de plus : « 1 Generate (free) » et « 2 Assemble (free) ».
- Les plans v1 et v2, les workflows v2 et les clips déjà générés restent utilisables.

## Nouveautés de la version 2

- **Résolution dans le workflow** : entrées `width` et `height` sur le nœud de clip, à relier à
  un sélecteur de résolution.
- **Plan choisi dans une liste**, avec un bouton pour parcourir et le glisser-déposer d'un
  fichier `.json` sur le nœud. Le fichier est copié dans `ComfyUI/input/plans/`.
- **Références par étiquette** : un nœud « H3 Plan - Reference » par image, avec un nom et une
  image déposée dessus. Plus de dossier ni de noms de fichiers imposés.
- Les plans de la version 1 se chargent toujours ; leur dossier de références est ignoré.
- Les clips déjà générés restent utilisables.

## Contenu

| Fichier | Rôle |
|---|---|
| `nodes.py` | Les sept nœuds ComfyUI et les adresses serveur du choix des prises |
| `core.py` | Lecture, validation et calcul des temps du plan (Python pur) |
| `web/h3_plan_upload.js` | Bouton et glisser-déposer pour choisir un plan ou une voix |
| `web/h3_plan_takes.js` | Interface du nœud de choix des prises (liste, lecteur vidéo) |
| `plan_tool.py` | Vérificateur en ligne de commande |
| `docs/PLAN_SPEC.md` | Spécification du format de plan |
| `docs/plan.schema.json` | Schéma JSON du plan |
| `docs/LLM_SYSTEM_PROMPT.md` | Prompt système pour faire écrire un plan par un LLM |
| `examples/` | Deux projets d'exemple : plans, images et chanson (voir Démarrage rapide) |
| `workflows/` | Génération puis assemblage, pour chacun des deux modes |
| `tests/test_core.py`, `tests/fixtures/` | Tests de la logique du plan et leurs plans de test |

## Prérequis

- ComfyUI avec le support natif de MiniMax H3 (nœud `MiniMaxH3ReferenceToVideo`).
- Le pack `ComfyUI-H3-Motion-Context-MultiRef` : il fournit l'injection de la chanson, la
  continuation image et son du mode `free` et l'assemblage final, que ces nœuds appellent.
- `ComfyUI-VideoHelperSuite`, pour l'écriture des vidéos.

Aucune dépendance Python supplémentaire.

## Installation ou mise à jour

Avec git (recommandé) :

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/VOTRE-COMPTE/ComfyUI-H3-ClipPlan.git
# mise à jour plus tard :
cd ComfyUI-H3-ClipPlan
git pull
```

Sans git : télécharger l'archive du dépôt (bouton « Code » → « Download ZIP ») et la décompresser
dans `ComfyUI/custom_nodes/ComfyUI-H3-ClipPlan` (le dossier doit porter ce nom, sans le suffixe
`-main`).

Puis :

1. Redémarrer ComfyUI et recharger la page du navigateur.
2. Ouvrir les workflows du dossier `workflows/` : ceux de la version 1 n'ont pas les nouvelles entrées.

Aucune dépendance Python à installer. Tests de la logique du plan, sans ComfyUI :
`python tests/test_core.py`.

## Utilisation en mode `free`

1. Ouvrir `workflows/H3 Clip Plan - 1 Generate (free).json` et vérifier les modèles. Il n'y a
   pas de chanson.
2. Choisir le plan sur le nœud « H3 Plan - Clip » (liste, bouton ou glisser-déposer).
3. Images : un nœud « REF … » par image. Voix : un nœud « VOICE … » par personnage, avec le même
   nom que son image. Tous les nœuds sont chaînés ; le dernier va dans `references`.
4. Régler la résolution, `clip_index` à 1 en `increment`, le nombre d'exécutions sur le nombre
   de clips, et lancer. L'aperçu de chaque clip contient le son généré.
5. Ouvrir `workflows/H3 Clip Plan - 2 Assemble (free).json`, choisir le même plan et lancer.
   Une musique de fond éventuelle s'ajoute ensuite au montage.

Pour le plan d'exemple `robot_workshop.plan.json`, il faut `robot` (image, voix facultative) et
`workshop` (image).

## Utilisation en mode `music_video`

1. Ouvrir `workflows/H3 Clip Plan - 1 Generate.json`, vérifier les modèles, charger la chanson.
2. **Plan** : sur le nœud « H3 Plan - Clip », cliquer sur `choose plan file (.json)` ou déposer
   le fichier sur le nœud. Il apparaît ensuite dans la liste `plan_file`.
3. **Références** : dans chaque nœud « REF … », déposer l'image voulue. Le champ `name` doit
   être le nom utilisé dans le plan. Le nœud « H3 Plan - Timeline » du second workflow affiche
   la liste des noms attendus avec leur description.
4. **Résolution** : la régler dans le sélecteur de résolution relié au nœud de clip.
5. Mettre `clip_index` à 1, laisser son contrôle sur `increment`, régler le nombre d'exécutions
   sur le nombre de clips du plan, et lancer.
6. Ouvrir `workflows/H3 Clip Plan - 2 Assemble.json`, charger la même chanson, choisir le même
   plan et lancer : la vidéo finale est écrite avec la chanson d'origine.

Tout est enregistré sous `ComfyUI/output/h3_clip_plan/<projet>/` :

```text
latents/clip_001/<prise>.pt         prises de chaque clip (voir « les prises »)
clips/clip_001_<nom>_<prise>_*.mp4  aperçu de chaque prise, recouvrement compris
selection.json                      prise choisie pour chaque clip
<projet>_final_*.mp4                vidéo finale
```

### Ajouter ou retirer une référence

Dupliquer un nœud « REF … », changer son nom et son image, puis l'insérer dans la chaîne : sa
sortie `references` va dans l'entrée `references` du nœud suivant, et le dernier de la chaîne
va dans le nœud de clip. L'ordre de la chaîne n'a pas d'importance : c'est le plan qui dit
quelles références chaque clip utilise et dans quel ordre.

### Refaire un clip

Mettre son numéro dans `clip_index`, passer le contrôle sur `fixed`, lancer une fois. Pour une
autre prise avec le même prompt, changer son `seed` dans le plan.

Un clip marqué `"continue": true` repart de la fin de la prise choisie du clip précédent. Si
un clip est refait, la nouvelle prise devient la prise choisie, et les prises des clips
`continue` qui suivent ne sont plus utilisables (elles restent construites sur l'ancienne) : il
faut les refaire, jusqu'au prochain clip de nouvelle scène, ou revenir à l'ancienne prise dans
le nœud de prises.

### Changer de résolution

Tous les clips d'un même projet doivent avoir la même taille. Après un changement de
résolution, il faut régénérer tous les clips, ou changer le nom `project` du plan pour repartir
d'un dossier vide.

### Vérifier un plan sans ComfyUI

```text
python plan_tool.py check examples/robot_beach.plan.json --song-duration 20
python plan_tool.py grid 22.5 40.1        instants de coupe possibles autour de ces temps
python plan_tool.py frames                longueurs de clip autorisées
```

## Les nœuds

| Nœud | Entrées | Sorties |
|---|---|---|
| **H3 Plan - Reference** | `name`, `image` (liste, envoi, glisser-déposer), `references` facultatif | `references` |
| **H3 Plan - Voice** | `name`, `voice_file` (liste, bouton `choose audio file`, glisser-déposer), `references` facultatif | `references` |
| **H3 Plan - Video** | `name`, `video_file` (liste des vidéos de `ComfyUI/input`), `references` facultatif | `references` |
| **H3 Plan - Clip** | `clip`, `vae`, `audio_vae`, `plan_file`, `clip_index`, `seed_mode`, et en option `master_audio` (obligatoire en `music_video`), `references`, `width`, `height` | `positive`, `latent`, `clip_audio` (silence en `free`), `seed`, `filename_prefix`, `plan_clip`, `info` |
| **H3 Plan - Save Clip Latent** | `latent` (sortie de l'échantillonneur), `plan_clip`, `delete_stale`, `audio_vae` facultatif | `latent`, `audio` (son généré, si `audio_vae` est relié), `report` |
| **H3 Plan - Assemble Final Video** | `video_vae`, `plan_file`, `last_clip`, `crf`, et `master_audio` (`music_video`) ou `audio_vae` (`free`) ; avec une vidéo de départ dans la vidéo finale : `references`, `width`, `height` et `audio_vae` | fichiers vidéo, `report` |
| **H3 Plan - Takes** | `plan_file` | liste des prises, aperçu vidéo, choix et ménage (dans le nœud) |
| **H3 Plan - Timeline** | `plan_file` | tableau des temps, noms des références attendues, état des clips |

### Câblage du workflow de génération

Si le workflow fourni ne se charge pas, le graphe se reconstruit à la main ainsi :

| De | Vers |
|---|---|
| Load CLIP | H3 Plan - Clip `clip` |
| Load VAE (vidéo) | H3 Plan - Clip `vae`, VAE Decode `vae` |
| Load VAE (audio) → H3 Audio VAE Compatibility | H3 Plan - Clip `audio_vae` |
| Load Audio (`music_video` seulement) | H3 Plan - Clip `master_audio` |
| Sélecteur de résolution `width`, `height` | H3 Plan - Clip `width`, `height` |
| Chaîne des H3 Plan - Reference (dernier nœud) | H3 Plan - Clip `references` |
| H3 Plan - Clip `positive` | BasicGuider `conditioning` |
| H3 Plan - Clip `latent` | SamplerCustomAdvanced `latent_image` |
| H3 Plan - Clip `seed` | RandomNoise `noise_seed` |
| SamplerCustomAdvanced `output` | H3 Plan - Save Clip Latent `latent` |
| H3 Plan - Clip `plan_clip` | H3 Plan - Save Clip Latent `plan_clip` |
| H3 Plan - Save Clip Latent `latent` | VAE Decode `samples` |
| VAE Decode | Video Combine `images` |
| H3 Plan - Clip `clip_audio` (`music_video`) ou H3 Plan - Save Clip Latent `audio` (`free`) | Video Combine `audio` |
| H3 Plan - Clip `filename_prefix` | Video Combine `filename_prefix` |

La chaîne du modèle (Load Diffusion Model → attention → LoRA → Sigma Shift → BasicGuider et
BasicScheduler) et le choix de l'échantillonneur restent ceux du workflow d'origine.

## Limites connues

- Les nœuds s'appuient sur le fonctionnement interne de trois nœuds existants. Une mise à jour
  de ComfyUI ou du pack MultiRef peut demander une adaptation.
- Si l'envoi d'un plan par le bouton échoue, copier le fichier dans `ComfyUI/input/plans/` et
  appuyer sur R pour rafraîchir la liste.
- 64 clips au maximum, limite du nœud d'assemblage.
- `crossfade_frames` s'applique à tous les raccords : coupes franches partout ou fondus partout.
- Sans `start_video`, le premier clip du plan commence toujours au début de la chanson.
- Mode `free` : une nouvelle scène après une coupe perd aussi ses 39 premières images (amorce
  cachée), comme en mode `music_video`. C'est du calcul en plus, mais cela garde un seul
  assembleur pour les deux modes.
- Mode `free` : la stabilité d'une voix sur une longue chaîne de continuations n'a pas encore été
  mesurée. Une voix générée sans échantillon peut changer d'un clip à l'autre.
- MiniMax H3 accepte 3 voix au plus par clip, 15 s au total : avec plusieurs voix, chaque
  échantillon est coupé à ses premières secondes (7,5 s pour deux voix, 5 s pour trois).

## Licence

GPL-3.0 (fichier `LICENSE`), comme le pack `ComfyUI-H3-Motion-Context-MultiRef` dont ces nœuds dépendent.
