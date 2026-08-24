# Workflows n8n recommandés

## 1. Relance quotidienne

Déclenchement chaque matin, lecture des tâches dues, regroupement par priorité, puis notification email ou Slack. L'envoi LinkedIn reste manuel.

## 2. Import d'une mission

Webhook n8n recevant titre, description, URL, entreprise, lieu et TJM, puis appel `POST /api/opportunities`. La réponse contient immédiatement le score et son détail.

## 3. Alerte mission prioritaire

Après création, si `score >= 75`, envoyer une notification avec le lien source et demander une validation avant préparation du CV.

## 4. Hygiène du pipeline

Chaque semaine, signaler les opportunités sans mise à jour depuis sept jours. Ne jamais envoyer automatiquement un CV adapté sans validation humaine.

## 5. Synchronisation des relations LinkedIn (source principale)

Utiliser Waalaxy comme déclencheur lorsqu'une invitation LinkedIn est acceptée. Le workflow n8n transforme le contact puis appelle :

`POST https://crm.osito-solution.com/api/imports/linkedin`

En-tête obligatoire : `X-Import-Key: {{$env.MISSIONFLOW_IMPORT_KEY}}`.

Corps attendu :

```json
{
  "leads": [{
    "name": "Prénom Nom",
    "headline": "Intitulé LinkedIn",
    "company": "Entreprise",
    "linkedin_url": "https://www.linkedin.com/in/identifiant/",
    "connected_on": "2026-08-24",
    "source": "LinkedIn / Waalaxy — import automatique",
    "stage": "a_contacter"
  }]
}
```

MissionFlow normalise l'URL LinkedIn, ignore les doublons, force le statut `À contacter` et recalcule le score. Le workflow doit journaliser `examined`, `created` et `duplicates`. Conserver le contrôle navigateur planifié comme mécanisme de rattrapage, et non comme source principale.
