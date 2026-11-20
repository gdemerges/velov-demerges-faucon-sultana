-- Utilisateur en lecture seule pour Grafana (S6, Should) : il lit la table predictions, rien d'autre.
-- Rejouable : lancé à chaque démarrage par le service grafana-db-init (compose.monitoring.yaml).
-- Le mot de passe arrive en variable psql (-v pw=...), jamais écrit dans le dépôt.
SELECT 'CREATE ROLE grafana_ro LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro')\gexec
ALTER ROLE grafana_ro WITH LOGIN PASSWORD :'pw';
-- Même si une requête tentait d'écrire, la transaction serait refusée.
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
REVOKE ALL ON SCHEMA public FROM grafana_ro;
GRANT CONNECT ON DATABASE velov TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON predictions TO grafana_ro;
