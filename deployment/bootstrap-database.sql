DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'osito_crm') THEN
    CREATE ROLE osito_crm LOGIN;
  END IF;
END
$$;

SELECT 'CREATE DATABASE osito_crm OWNER osito_crm'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'osito_crm')\gexec

REVOKE ALL ON DATABASE osito_crm FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE osito_crm TO osito_crm;

