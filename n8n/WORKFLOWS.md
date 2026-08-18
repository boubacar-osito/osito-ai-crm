# Workflows n8n recommandés

## 1. Relance quotidienne

Déclenchement chaque matin, lecture des tâches dues, regroupement par priorité, puis notification email ou Slack. L'envoi LinkedIn reste manuel.

## 2. Import d'une mission

Webhook n8n recevant titre, description, URL, entreprise, lieu et TJM, puis appel `POST /api/opportunities`. La réponse contient immédiatement le score et son détail.

## 3. Alerte mission prioritaire

Après création, si `score >= 75`, envoyer une notification avec le lien source et demander une validation avant préparation du CV.

## 4. Hygiène du pipeline

Chaque semaine, signaler les opportunités sans mise à jour depuis sept jours. Ne jamais envoyer automatiquement un CV adapté sans validation humaine.
