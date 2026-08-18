# Configurer Google OAuth

1. Ouvrir Google Cloud Console et choisir le projet OAuth OSITO existant, ou créer un projet dédié.
2. Configurer l'écran de consentement avec le nom `MissionFlow`.
3. Créer un client OAuth de type **Application Web**.
4. Ajouter l'origine autorisée `https://crm.osito-solution.tech`.
5. Ajouter exactement l'URI de redirection `https://crm.osito-solution.tech/auth/google/callback`.
6. Placer l'identifiant et le secret dans `/opt/osito-crm/.env` sur le VPS.
7. Définir `GOOGLE_ALLOWED_EMAIL` avec l'unique adresse autorisée.

Google exige une correspondance exacte de l'URI de redirection. Le protocole, le domaine, le chemin et l'éventuel slash final doivent être identiques.

