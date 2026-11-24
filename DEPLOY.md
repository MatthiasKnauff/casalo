# Casalo



## So kommt eine Änderung auf die Server

1. **Push auf `main`** – GitHub baut das Image. Die Pipeline schreibt die fertige Compose-Datei in den Zweig
   `deploy-test`, Portainer bemerkt das (spätestens nach einer Minute) und aktualisiert den **Testserver**.
2. **Testserver ansehen** – oben steht „Testserver – wird vX.Y.Z“.
3. **Release freigeben** – [neues Release anlegen](https://github.com/MatthiasKnauff/casalo/releases/new?target=main).
   Die Pipeline baut das Image mit der Versionsnummer, schreibt es in `deploy-prod`, und Portainer
   aktualisiert den **Produktivserver**.

| | Server |
|---|---|
| Test | docker3 · Port 8081 · http://192.168.131.23:8081 · Volume `casalo-test` |
| Produktiv | docker3 · Port 8080 · https://casalo.knauff.info · Volume `casalo-prod` |

## Aufbau

- `deploy/compose.template.yml` – Compose-Datei für beide Server (Ports und Variablen kommen aus Portainer)
- `.github/workflows/deploy.yml` – Pipeline
- Zweige `deploy-test` / `deploy-prod` – werden von der Pipeline geschrieben, nicht von Hand ändern

Eingerichtet mit der Deploy-Automatik.
