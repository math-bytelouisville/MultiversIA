# MultiversIA

Outils locaux pour explorer une représentation commune entre les langages informatiques, modifier des modèles mathématiques et reconstruire du code. Le **Game Editor QCM** sert de cas concret pour faire évoluer cette architecture.

## Téléchargements

**Mise à jour du 20 novembre 2026 :** les sources du QCM incluent désormais la personnalisation des questions, des images, de la disposition et des règles, ainsi que les corrections des audits fonctionnel et sécurité. Voir le [guide de lancement et le bilan](sources/SYNTHESE_MATHS_CODE_V0.2.0/QCM_2026-11-20.md). Les archives ci-dessous restent celles du 15 novembre ; elles ne contiennent pas ces correctifs.

| Je veux… | Télécharger | Prérequis et portée |
| --- | --- | --- |
| Essayer le **Game Editor QCM V0.2.0** | [Game Editor pour Windows](https://github.com/math-bytelouisville/MultiversIA/releases/download/suite-2026-11-15/Game-Editor-V0.2.0-Windows.zip) | Windows, PowerShell, .NET Runtime 10 et ASP.NET Core Runtime 10. Application compilée, sources et guide inclus. |
| Consulter **toutes les dernières sources** | [Suite de sources du 15 novembre 2026](https://github.com/math-bytelouisville/MultiversIA/releases/download/suite-2026-11-15/MultiversIA-sources-2026-11-15.zip) | Sources, tests et guides. Les outils de migration demandent une préparation de leur environnement ; ce ZIP n'est pas un installateur autonome. |
| Vérifier les téléchargements | [Empreintes SHA-256](https://github.com/math-bytelouisville/MultiversIA/releases/download/suite-2026-11-15/SHA256SUMS.txt) | Comparer avec `Get-FileHash fichier.zip -Algorithm SHA256` dans PowerShell. |

[Voir la publication et ses notes](https://github.com/math-bytelouisville/MultiversIA/releases/tag/suite-2026-11-15).

## Essayer le Game Editor

1. Télécharger et extraire entièrement le ZIP Game Editor.
2. Installer **.NET Runtime 10 et ASP.NET Core Runtime 10 pour Windows** si nécessaire, depuis [Microsoft](https://dotnet.microsoft.com/en-us/download/dotnet/10.0). Le seul runtime .NET de base ne suffit pas.
3. Lire `LIRE-MOI.txt`, puis ouvrir `Lancer-Game-Editor.cmd`.
4. Garder sa console ouverte pendant l'utilisation ; `Ctrl+C` arrête le serveur.

Le Studio fonctionne sur cet ordinateur avec un compte local : création de QCM, questions à choix unique ou multiple, barème configurable, suivi des parties, résultats et classements. La carte relie les points d'intérêt et permet de zoomer librement. Les nouvelles données sont enregistrées localement ; aucune partie personnelle n'est fournie dans le ZIP.

## Aller directement à un sujet

| Projet | Dernière version incluse | Ce qu'elle contient |
| --- | --- | --- |
| [Migrateur local](sources/MIGRATEUR_LOCAL_V0.5.0) | V0.5.0 | Rust/C# → modèle commun → Python/C#/Rust ; correction locale bornée ; opérations Int32 modulo explicites. |
| [Game Editor / QCM Studio](sources/SYNTHESE_MATHS_CODE_V0.2.0/qcm_studio) | V0.2.0 | Application MVC ASP.NET Core et éditeur de QCM extensible. |
| [Atelier maths → code](sources/SYNTHESE_MATHS_CODE_V0.2.0) | V0.2.0 | Fonctions pures, modules, contrats, provenance et révisions mathématiques. |
| [Migration Studio](sources/MIGRATION_STUDIO_V0.3.0) | V0.3.0 | Migration du calcul des scores QCM vers Rust sur un profil borné. |
| [Laboratoire](sources/LABORATOIRE_MULTIVERSIA_V0.2.0) | V0.2.0 | Expériences et évaluation locales, pont historique C#/Lean. |
| [Carte de suivi](sources/CARTE_MULTIVERSIA_V0.1.0) | V0.1.0 | Carte avec zoom libre et consultation du journal. |
| [Cycle durable](sources/CYCLE_DURABLE_V0.1.0) | V0.1.0 | Simulation, journal et reprise après interruption de processus. |
| [Architecture](sources/ARCHITECTURE_CHANTIER_2026-11-15) | Document du 15 novembre 2026 | Schéma de conception historique ; certaines capacités ont évolué depuis. |

## État et limites

Le migrateur traite un **sous-ensemble explicite de fonctions pures Int32/bool**. Il ne traduit pas encore un site complet ni l'ensemble de C#, Rust ou .NET. Ses comparaisons d'exécution portent sur des cas finis ; elles ne constituent pas une preuve universelle.

La version locale V0.5.0 possède deux revues PASS par composant, 20 tests de régression PASS et huit scénarios arithmétiques qualifiés. Deux assertions de test sur Python interprété ont été corrigées puis revalidées sur les reçus réels ; l'échec initial est conservé dans le dossier de preuves d'origine. Ces résultats ne qualifient pas automatiquement une nouvelle machine.

Les parcours Roslyn, Lean et Rust de l'archive de sources dépendent de manifestes et reçus locaux qui ne sont pas distribués. Lire [les dépendances et limites de l'archive](sources/LIRE_AVANT_SOURCES.txt) avant de tenter ces parcours. Le paquet Game Editor possède son propre lanceur de distribution.

Le [manifeste du 15 novembre](sources/MANIFEST_SOURCES.json) décrit la distribution historique de cette date, pas les sources mises à jour depuis. Le [manifeste QCM du 20 novembre](sources/SYNTHESE_MATHS_CODE_V0.2.0/QCM_SHA256_2026-11-20.json) trace les fichiers de cette mise à jour. Les données d'utilisation, secrets, SDK et journaux locaux ne sont pas publiés.

## Accès

Ce dépôt est public pour consultation et téléchargement. Aucun droit d'écriture n'est accordé aux visiteurs. Aucune licence open source n'a été ajoutée à cette publication.
