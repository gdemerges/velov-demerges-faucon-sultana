#!/usr/bin/env bash
# Certificat TLS auto-signé pour l'adresse IP de la VM (S6).
#   ./scripts/make_cert.sh 192.168.1.237
# Le certificat (certs/velov.crt) se donne aux clients ; la clé (certs/velov.key) ne quitte pas la VM.
set -euo pipefail
IP="${1:?usage : scripts/make_cert.sh <ip-de-la-vm>}"
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 90 \
  -keyout certs/velov.key -out certs/velov.crt \
  -subj "/CN=velov-${IP}" -addext "subjectAltName=IP:${IP}"
chmod 600 certs/velov.key
openssl x509 -in certs/velov.crt -noout -subject -ext subjectAltName -enddate
