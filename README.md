# MediTools schedule

Remote control for the bedside medicine device. Edit `schedule.json` (on
github.com or the GitHub mobile app) and commit — the device picks the change
up within ~10 minutes. Invalid JSON is rejected on the device; the old
schedule keeps running.

| Key | Meaning |
|---|---|
| `dose_times` | `"HH:MM"` medicine times (currently 07:00–22:00, six doses) |
| `no_eat_before_min` / `no_eat_after_min` | red window around each dose (minutes) |
| `night.start` / `night.end` | dimmed night mode |
| `alarm.max_minutes` | alarm gives up and logs "missed" after this |
| `language` | `nl` or `en` |
