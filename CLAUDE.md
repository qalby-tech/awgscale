# awgscale

A two-tier AmneziaWG VPN cascade (an entry server, exits abroad) with an admin
panel, a Telegram bot and a smart DNS. Everything on the servers runs in
containers; `stack/deploy.sh` ships it, `tests/run.sh` checks it.

The panel page is a Next.js application in `web/` (TypeScript, static export).
`web/build.sh` builds it in a Node container into `web/out`; the panel server
(`awgscale.panel`) hands those files out next to its `/api`. The browser tests
(`tests/*.test.js`) run against that build and a stand-in server (`tests/stub.py`).

## Layout

* `src/awgscale/<name>/` — one package per container, run as `python -m awgscale.<name>`: `panel`, `agent`, `bot`, `dnsgate` (the container `smartdns`), `users`, `watch`, `tunnel` (also the container `links`), `resolver`, `exits`. `core/` and `service/` are what they share. What the exits service runs on an exit server (`exits/remote/`, with `exit-firewall.sh` and the stats service) and its `askpass.sh` live inside its package.
* `docker/Dockerfile` — the one image recipe; `docker/build.sh` builds `awgscale-<name>:local` for every service on this machine (the server builds nothing).
* `stack/compose.yml` — the stack on the entry server; `stack/deploy.sh [<service> …]` ships the images and the files and switches only the services named (none named: nothing running changes); `stack/probes.sh` — the questions it asks a service before it believes it; `stack/switch-tunnel.sh`, `stack/deadman.sh` — who keeps the tunnels, and the reboot safety net.
* The boot gate: `stack/cascade-apply.service` runs `/opt/vpn-stack/boot/gate.pyz apply` on the host's bare python3 before Docker exists. The file is made by `python -m awgscale.watch.bundle` (the gate and what it imports, standard library only) and is taken out of the running watch container by the deploy.
* `panel/invite.html`, `panel/fonts/` — go into the panel's image; `cascade/cascade.conf`, `cascade/default-exit-list*` — the seeds of a new server; `cascade/*.sh`, `ops/`, `entry/` — operator scripts run from this machine.
* `tests/run.sh` — everything; `tests/deploy_remote.sh` — the server-side part of the deploy against stand-ins; `tests/stack.sh` — every image started the way `compose.yml` starts it, in a throwaway network namespace, and asked the deploy's own questions.
* Nothing in this repository is run against the servers by a test; `stack/deploy.sh`, `stack/switch-tunnel.sh`, `cascade/*.sh` and `ops/*.sh` are the scripts that do reach them (through `ssh_config`).
* `ssh_config` and `site.env` (the entry's name, the panel's allow-list, the addresses that need no DNS code) are the operator's own and gitignored; `ssh_config.example` and `site.env.example` show what they hold. The scripts read `site.env` through `site.sh`. No server address or the site's real name goes into a tracked file.

## Git

* Author and committer identity: every commit must have BOTH fields set to `XvKuoMing <80534052+XvKuoMing@users.noreply.github.com>`. The harness defaults committer to `Claude <noreply@anthropic.com>`, so the `--author` flag alone is not enough — also set the committer via env vars (do NOT modify git config):

```
GIT_COMMITTER_NAME="XvKuoMing" \
GIT_COMMITTER_EMAIL="80534052+XvKuoMing@users.noreply.github.com" \
  git commit --author="XvKuoMing <80534052+XvKuoMing@users.noreply.github.com>" -m "..."
```

When fixing existing commits, use the same env vars in a rebase exec:

```
GIT_COMMITTER_NAME="XvKuoMing" GIT_COMMITTER_EMAIL="80534052+XvKuoMing@users.noreply.github.com" \
  git rebase <base> --exec 'GIT_COMMITTER_NAME="XvKuoMing" \
    GIT_COMMITTER_EMAIL="80534052+XvKuoMing@users.noreply.github.com" \
    git commit --amend --no-edit \
      --author="XvKuoMing <80534052+XvKuoMing@users.noreply.github.com>"'
```

* Branch names: never prefix with `claude/`. Use plain descriptive slugs (e.g. `proxy-mock-only`, `vault-defaults-cleanup`). If a session starts you on a `claude/`-prefixed branch, rename or move the work to a clean branch before pushing.
* Commit messages: do NOT append the `https://claude.ai/code/...` footer, `Co-Authored-By: Claude`, `Generated with Claude Code`, or any other AI attribution trailer. Plain conventional commit messages only.
* Pull requests: same rule — no Claude footer in the PR body.

## Never commit

Keys, tokens and passwords stay out of the repository (see `.gitignore`):
users' configs (`clients/`), the bot token and the panel password
(`panel/.bot-token`, `panel/.password`), the admin's Telegram id
(`bot/admin-id`), local backups (`.backup-*`) and run logs (`logs/`).

## Code style

The owner reads the code and has set these rules. They apply to every new line.

### Python (`src/awgscale/`)

* uv project, all code under `src/`; every setting comes from `.env` through pydantic-settings and is listed in `.env.example`. Each service has `settings.py` with its settings class and `@lru_cache def get_settings()`.
* Objects are pydantic models or dataclasses, never bare dicts. Every field of a model or of a settings class is declared with `Field(...)`: default, description, constraints.
* Strings and numbers that mean something are enums or named constants. Message texts are the exception: an error text is written inline where it is raised or returned, not kept in a constant.
* Moments are `datetime` (aware, UTC) in models and `timestamptz` in the database, never Unix-second integers.
* Services are asynchronous. HTTP is FastAPI used the standard way: module-level `router = APIRouter()` with `@router.get(...)` functions, module-level `app = FastAPI()` in `app.py`, typed request bodies, FastAPI's own 422 for a request that does not fit its model, the service object through `Depends(get_service)` (`@lru_cache`). No factory functions that wrap routes or the application.
* Events, not polling loops: timed work is an APScheduler job, kernel state is listened to (`ip monitor link`). What runs for the life of the process is started in `__main__`, not in a FastAPI lifespan.
* Attributes nobody outside should read are `self.__name` (two underscores); what others may read is a `@property`. Full names, no abbreviations (`self.__settings`, not `self._s`).
* One assignment per line; no `a, b = x, y` to save lines (unpacking a function's result is fine).
* Regular expressions are readable: named groups, `re.VERBOSE`, a commented example of the line they match.
* Behaviour and user-facing texts are ported verbatim; a change of behaviour is proposed separately, never slipped in.
* The exception to "async and pydantic": what must run on the entry's bare Python before Docker starts (the boot gate, `src/awgscale/core/`) is synchronous and standard-library only.

### TypeScript (`web/`)

* Strict TypeScript, no `any`, no type assertions through `unknown`; payloads of the server are typed in `src/lib/types.ts`. A value that means something is an enum: code never compares against a bare string (`state === ExitState.Up`, `event.key === Key.Escape`, `presenceOf(client) === Presence.Paused`) — the linter refuses a string literal in a comparison.
* One declaration per statement (`const a = 1;` and `const b = 2;`, not `const a = 1, b = 2;`), full names instead of one-letter ones, lines within the formatter's width. `npm run lint` and `npm run format:check` must pass (ESLint and Prettier; `web/build.sh lint`).
* Small components and pure functions; state in React or the stores, not manual DOM patching.
