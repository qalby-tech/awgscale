"""The Telegram bot of the cascade. It runs next to the panel and talks to the same agent.

Only the admin is answered, and only in the private chat; everyone else is
ignored silently.

Commands
  /status           состояние каскада
  /users [часть имени]  пользователи: первые сорок, или найденные
  /add имя [срок]   новый пользователь (срок: 12h, 7d, 30d, 4w) → QR, строка, файл
  /key имя          прислать QR, строку и файл ещё раз
  /del имя          удалить пользователя (с подтверждением)
  /invite [устройств] [дней] [группа]   ссылка-приглашение
  /report           сводка за текущие сутки, не дожидаясь утра
  /help

By itself (scheduled jobs)
  the watch   — alerts: an exit down or up, all exits down, the users' tunnel
                down, the agent unreachable, the ad blocker down, sustained high
                load, memory or disk nearly full, a key expiring within a day;
                and what was changed in the panel, said on to the admin
  the sample  — exit downtime and peak online, kept in stats.json (the
                users' traffic of the day is counted by the agent: `day`);
                once a day at the report hour the daily summary is sent and
                the day's counters start again

    python -m awgscale.bot
"""
