# MissionFlow

Mini-CRM personnel pour transformer des opportunités Salesforce en mission contractualisée.

Production prévue sur `https://crm.osito-solution.tech`, avec authentification Google OAuth et accès mobile.

## Ce que couvre la V1

- pipeline Kanban des missions ;
- score de compatibilité explicable sur 100 ;
- profil candidat de référence (compétences, rôles, localisation, TJM) ;
- analyse ATS et résumé adapté, sans ajout de compétence non démontrée ;
- API documentée automatiquement sur `/docs` ;
- déploiement Docker avec PostgreSQL ;
- points d'entrée simples pour les workflows n8n.
- authentification Google OpenID Connect limitée à une adresse autorisée ;
- configuration Traefik compatible avec l'infrastructure OSITO.

## Documentation

- [Architecture technique](docs/architecture.md)
- [Configuration Google OAuth](docs/google-oauth.md)
- [Automatisations n8n](n8n/WORKFLOWS.md)

## Lancer localement

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Ouvrir `http://localhost:8000`. Sans variable d'environnement, l'application utilise SQLite pour faciliter le développement.

## Déployer sur le VPS

1. Copier `.env.example` vers `.env`.
2. Définir un mot de passe PostgreSQL fort dans `POSTGRES_PASSWORD` et une longue valeur aléatoire dans `SECRET_KEY`.
3. Définir `CORS_ORIGINS` avec le domaine HTTPS du CRM.
4. En local, lancer `docker compose up -d --build` derrière un reverse proxy HTTPS.

En production OSITO, utiliser `docker-compose.prod.yml` avec le réseau externe `osito-net`. Les secrets sont conservés dans `/opt/osito-crm/.env`, jamais dans GitHub.

Ne pas exposer PostgreSQL publiquement. Prévoir une sauvegarde quotidienne du volume et ajouter une authentification avant toute exposition Internet.

## Scoring

Le score est volontairement lisible : compétences 45 points, rôle 20, écosystème Salesforce 15, localisation 10 et TJM 10. Toute modification du profil recalcule les missions.

## n8n

Le dossier `n8n/` décrit les automatisations conseillées. n8n peut appeler l'API interne du service `app` avec `http://app:8000/api/...` s'il rejoint le même réseau Docker.
