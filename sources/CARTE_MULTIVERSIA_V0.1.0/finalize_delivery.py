"""Record this delivery's checked scope; never modify previous versions."""
import hashlib
import json
from pathlib import Path
import backend

root=Path(__file__).resolve().parent
historical=json.loads((root.parent/'CYCLE_DURABLE_V0.1.0/evidence/test-results.json').read_text(encoding='utf-8'))
preserved={name:hashlib.sha256((root.parent/'CYCLE_DURABLE_V0.1.0'/name).read_bytes()).hexdigest()==digest for name,digest in historical['sources'].items()}
assert all(preserved.values())
architecture=root.parent/'ARCHITECTURE_CHANTIER_2026-11-15/architecture.txt'
architecture_hash=hashlib.sha256(architecture.read_bytes()).hexdigest()
assert architecture_hash=='c13fa45a6510165fafac3a260ecf86ccd89dfad542d27fa9ce7ab1d911013d99'
tests=json.loads((root/'evidence/test-results.json').read_text(encoding='utf-8'))
assert tests['tests']==15 and tests['failures']==0 and tests['errors']==0
shortcut=json.loads((root/'evidence/desktop-shortcut.json').read_text(encoding='utf-8-sig'))
assert shortcut['verified'] and Path(shortcut['shortcut']).is_file()
visual={
 'scope':'Contrôle visuel via computer-use, fenêtre de test isolée puis application finale',
 'observed':['Sélection sur la carte','Zoom à la molette','Déplacement par glisser','Reprise réelle de la simulation interrompue','Trois états distincts : terminé, écart de prédiction, résultat à reprendre','Raccourci lancé et fenêtre finale ouverte','Parcours des événements à 135 %','Sélection de la preuve archivée et affichage de son empreinte'],
 'fixed':['Centrage de la mission','Détail des textes selon échelle','Parcours de preuves séparé du résumé'],
 'limits':['Résolutions et accessibilité non exhaustivement qualifiées','Historique existant consulté sans commande de mutation','Aucune validation scientifique ni inférence']}
backend.atomic(root/'evidence/visual-review.json',visual)
files=[p for p in root.iterdir() if p.is_file() and p.name not in {'checkpoint.json'}]
manifest={'created_at':backend.now(),'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in files},'old_cycle_sources_preserved':preserved,'architecture_sha256':architecture_hash,'tests':tests,'shortcut':shortcut}
backend.atomic(root/'evidence/delivery-manifest.json',manifest)
backend.atomic(root/'checkpoint.json',{
 'date':'2026-11-15','status':'interface_locale_livree_et_ouverte','objective':'Carte librement zoomable et déplaçable, accès Bureau, suivi réel sans surcharge visuelle',
 'user_choice':'Grand schéma avec zoom libre','quota_exception':'Dérogation temporaire de la mission architecture maintenue dans cette continuation ; aucun reset utilisé.',
 'entrypoint':shortcut['shortcut'],'fallback':'Lancer MultiversIA.cmd','guide':'GUIDE.txt','architecture_review':'REVUE_ARCHITECTURE.txt',
 'implemented':['Carte de 25 % à 280 %','Déplacement et sélection','Détail progressif, parcours des événements à partir de 115 %','Lecture périodique vérifiée des journaux','Historique V1 en lecture seule','Nouvelles missions isolées','Commandes en sous-processus et reprise','Résumé, journal, données brutes, export','Position et sélection conservées au redémarrage','Architecture intégrale accessible'],
 'tests':tests,'visual_review':'evidence/visual-review.json','manifest':'evidence/delivery-manifest.json',
 'original_preservation':{'cycle_sources':preserved,'architecture_unchanged':True,'readonly_reader_preservation_test':True},
 'remaining_for_interface_delivery':[],
 'architecture_still_under_construction':['Schéma V2 et migration explicite','Diagnostic causal automatique','Mémoire de connaissances révisables','Évaluation scientifique séparée','IA locales et vérificateurs raccordés après qualification'],
 'next_step':'Poursuivre le jalon V2/diagnostic de l’architecture dans une nouvelle version isolée ; raccorder ensuite ses événements réels à la carte.',
 'limitations':['Simulateur local, pas activité réelle des modèles','Pas de surveillance globale du PC ou de tous les chats Codex','Aucun gain scientifique démontré','Architecture complète conservée mais non entièrement implémentée']})
print(json.dumps({'status':'DELIVERED','tests':tests['tests'],'shortcut':shortcut['shortcut'],'originals_preserved':all(preserved.values())},ensure_ascii=False))
