# MultiversIA — premier cycle durable V0.1.0

Ce prototype réalise une première partie de la conception **Hybride V0.1.6**. Le numéro V0.1.0 désigne le logiciel expérimental, pas une révision du document d'origine. Il utilise Python et sa bibliothèque standard déjà présents sur ce PC.

## Ouvrir l'application

Double-cliquer sur **Lancer MultiversIA.cmd** dans ce dossier. Une fenêtre de console peut rester ouverte derrière l'application ; la fermer arrête aussi l'application. Les données personnelles de cette démonstration sont créées dans `data/`, séparément des preuves de test.

1. Choisir les positions A et B.
2. Prédire si la lampe sera allumée ou éteinte.
3. Choisir une panne éventuelle et cliquer sur **Lancer l'expérience**.
4. Après une panne, sélectionner l'expérience puis **Reprendre la sélection**.
5. Utiliser **Exporter le journal HTML** pour obtenir une copie lisible dans le navigateur.

Pour essayer la panne importante, choisir **Après l'effet, avant le résultat**. La reprise doit retrouver le reçu : le compteur d'effets ne doit pas augmenter. Le compteur compte les effets **simulés dont le reçu existe**, pas un nombre d'effets physiques observés indépendamment.

Avec **Après l'envoi, avant l'effet**, aucun reçu n'existe. La reprise reste donc **RÉSULTAT_À_VÉRIFIER**, même si le testeur sait que la panne précédait l'effet. C'est volontaire : le contrôleur ne peut pas déduire cette information de son seul journal. Une nouvelle expérience possède une nouvelle identité ; ce n'est pas la reprise de l'ancienne.

## Ce que les états signifient

| État | Ce qui est connu |
|---|---|
| PRÉPARÉE | Entrées et prédiction conservées ; aucune intention d'envoi enregistrée. |
| INTENTION_ENREGISTRÉE | Intention conservée ; aucun envoi enregistré. Le contrôleur peut reprendre après nouveaux contrôles. |
| EN_COURS | Envoi enregistré ; vérifier le reçu avant toute conclusion. |
| RÉSULTAT_À_VÉRIFIER | L'effet peut avoir eu lieu. Aucune répétition automatique. |
| TERMINÉE | Reçu et observation vérifiés. La prédiction peut être fausse. |
| ÉCHEC_DOCUMENTÉ | Le simulateur a conservé la preuve d'un échec avant effet. |
| REFUSÉE | Un contrôle a refusé l'expérience avant son effet. |

Le journal conserve la prédiction initiale. Une nouvelle hypothèse se teste par une nouvelle expérience, sans réécrire la précédente. L'utilisateur propose les prédictions : aucun agent ni moteur d'apprentissage n'est intégré.

## Conservation et reprise

- `journal.sqlite3` : événements ordonnés, horodatés, reliés par SHA-256. Les états affichés sont reconstruits depuis ces événements.
- `journal-head.json` : témoin séparé de la dernière empreinte, permettant de détecter notamment la suppression de la fin du journal.
- `receipts.sqlite3` : registre du simulateur, dans une transaction différente de celle du journal. La même clé d'opération ne crée pas un second reçu.
- `policy.json` : permissions et budgets locaux ; fichier absent ou invalide = refus d'effet, jamais autorisation implicite.
- `installation.json` : marqueur de version du dossier. Une base disparue n'est pas recréée automatiquement.
- `controller.lock` : empêche deux contrôleurs coopératifs de modifier ce dossier simultanément.

Le sous-processus expérimental est réellement interrompu par `os._exit(86)` aux points de panne. L'interface reste ouverte pour permettre la reprise. Les tests relancent ensuite un **nouveau processus Python**.

En cas de corruption, conserver tout le dossier pour analyse. Ne pas supprimer une base pour « débloquer » une mission. Une copie de sauvegarde doit être prise lorsque les processus sont arrêtés et doit contenir le dossier de données entier.

## Tests et preuves

Depuis ce dossier :

```powershell
python run_checks.py
```

Le résultat structuré est enregistré dans `evidence/test-results.json`. Il contient les noms des tests, leur résultat, les empreintes des sources et le chemin des données de test conservées. Chaque nouvelle exécution garde un dossier distinct.

Les tests couvrent T01–T11 du plan dans le périmètre du simulateur : prédiction juste/fausse, arrêts avant et après intention, arrêt après effet, reçu absent, doublons, référence inventée ou incorrecte, corruption et troncature, révocation, budgets, capteurs et protocoles séparés. Les contrôles de capteur manquant ou de RAM élevée emploient des fixtures explicitement identifiées. **T12 Lean est différé**, car il nécessite un autre composant.

L'interface a également fait l'objet d'une inspection visuelle : une première disposition masquait son pied de page ; la disposition corrigée laisse visibles le journal, la reprise et l'export. Trois tests internes Tk, hors écran, vérifient le lancement depuis le bouton, la reprise après une panne réelle du processus et la restauration de l'historique au redémarrage. Cela ne constitue pas une validation de toutes les interactions à la souris, de toutes les résolutions ou de tous les paramètres d'accessibilité.

## Ligne de commande

```powershell
python engine.py --data data init
python engine.py --data data run --a 1 --b 0 --prediction 1
python engine.py --data data run --a 1 --b 0 --prediction 0 --crash after_effect
python engine.py --data data status
python engine.py --data data resume ACT-identifiant-affiche-par-status
python engine.py --data data report
```

`--operation-key` sert à représenter le même message reçu plusieurs fois. Une même clé avec un contenu différent est refusée. `--receiver-failure` crée une preuve d'échec simulé avant effet. `--claims-json` accepte uniquement des références exactes du type `[{"evidence_id":"OBS-ACT-...","lamp_on":true}]` ; le registre doit contenir cette observation et cette valeur.

Les versions de protocole sont enregistrées par l'API locale `Engine.register_protocol` sous le verrou `exclusive`. Une version existante est immuable ; les versions supplémentaires gardent la même expérience booléenne dans ce prototype. L'interface ne propose pas encore d'éditeur de protocole.

## Limites explicites

- **Simulation seulement.** La loi déterministe de la lampe est dans le code du simulateur. Elle n'est pas protégée contre sa lecture et aucun test d'apprentissage avec solution cachée n'est revendiqué.
- Le simulateur écrit l'effet et son reçu **dans sa propre transaction SQLite**. Cela ne prouve pas qu'un appel LM Studio, une écriture de fichier externe ou un service réseau sera exécuté exactement une fois.
- Les interruptions de processus ne sont pas des essais de coupure électrique, de panne disque ou de cache matériel. `synchronous=FULL` et `fsync` sont utilisés, sans certification matérielle.
- Journal et témoin de fin ne sont pas une seule transaction. Une panne entre leur mise à jour peut provoquer un arrêt conservateur. Aucun mécanisme automatique ne « répare » cette divergence.
- Les empreintes détectent les incohérences testées. Un opérateur capable de réécrire toutes les bases et le témoin peut falsifier l'ensemble. Les horodatages viennent de l'horloge locale : ils ne sont pas une preuve temporelle indépendante.
- Les permissions sont une politique applicative, **pas une sandbox Windows**. Le verrou protège les contrôleurs qui le respectent, pas un autre logiciel modifiant directement les fichiers. Le script doit rester de confiance.
- Le garde mesure réellement la RAM Windows et le disque avant l'effet ; il refuse à partir de 90 % de RAM, ou d'une borne plus basse configurée. Il ne surveille pas continuellement le PC et ne garantit aucun plafond CPU/GPU/VRAM. Aucun modèle n'est autorisé par ce garde.
- Budget par défaut : 100 actions, seuil de stockage de 20 Mo, au moins 64 Mo libres. Le maximum du prototype est 200 actions par dossier. Les refus restent enregistrés : le seuil de stockage n'est pas une limite dure du système de fichiers.
- Un travail lancé par l'interface a un délai de 15 secondes. Après un délai dépassé, utiliser **Actualiser**, puis la reprise. Ce délai n'est pas un budget global de mission ou de quota Codex. Les limites Codex ne sont pas intégrées à l'application.
- Aucun modèle, installation, accès réseau, C#, Lean, intégration à Project Zomboid ou à un autre composant MultiversIA n'a été utilisé pour cette livraison.

Les fichiers d'origine et les autres travaux du projet sont préservés. Le point de reprise de cette mission est `checkpoint.json`.
