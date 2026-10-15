# Sanitized source snapshot

This repository contains a sanitized copy of the CRM Detailing source tree for code review and future deployment. It is not a production data backup.

Removed from this copy: real `.env` files and provider credentials, database dumps, customer-uploaded media, generated build/static output, logs, Git history, private keys/certificates, local caches, and the original project brief. Owner name, tax ID, and personal Gmail address were replaced with neutral placeholders in copied source/templates.

The example environment files contain placeholders only. Before deployment, create a new `.env.production` from `.env.production.example`, provide fresh secrets through a secure channel, and restore business data from a separately protected backup. Do not commit secrets or customer data.
