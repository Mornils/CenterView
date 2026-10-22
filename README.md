# Center View – Wochenplan-Bildschirm

```sh
docker compose up -d --build
```

- Anzeige: `http://<host>:8080/` (Wochenansicht), `http://<host>:8080/#day` (Tagesansicht pro Person)
- Einstellungen: `http://<host>:8080/settings` – Personen, Farben, CalDAV-Konten, ICS-Feeds, sichtbare Stunden
- Kalender ein/aus: auf die Chips oben in der Anzeige tippen (wird serverseitig gespeichert)
- Konfiguration liegt in `./data/config.json` (enthält CalDAV-Passwörter im Klartext)
- `ADMIN_PASSWORD` in `docker-compose.yml` setzen, um die Einstellungen zu schützen

CalDAV-URL: meist reicht die Server-Basis, z. B. Nextcloud `https://cloud.example.com/remote.php/dav`
(alle Kalender des Kontos erscheinen als Chips). Eine einzelne Kalender-URL geht auch.

Kiosk (z. B. Raspberry Pi): `chromium --kiosk --noerrdialogs http://<host>:8080/`
# CenterView
