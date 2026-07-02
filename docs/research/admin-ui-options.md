# Admin UI Options for Allan Edwards Product Catalog

**Task:** T234  
**Date:** 2026-07-02  
**Scope:** Should the Allan Edwards quote tool use a separate Django admin instance (like TrueVi) for product catalog and admin data CRUD?

---

## 1. TrueVi Django Admin Pattern

TrueVi (`page-api`) runs a Django admin instance inside the same repo as its Flask app. Key facts:

**Layout**: `admin_django/` subdirectory in the `page-api` repo. Contains `settings.py`, `urls.py`, `wsgi.py`, per-app `admin.py` and `models.py` files, and Django migrations for Django-owned tables only.

**Process model**: A second gunicorn process runs on `127.0.0.1:8002` as a separate systemd service (`page-api-admin.service`). Flask (`page-api.service`) runs on a different port. Nginx proxies `/admin` to 8002 and serves `/admin/static/` from collected static files.

**Dual-ORM pattern**: All application models (`Series`, `UserEvent`, `TheologyDocument`, etc.) use `managed = False`. Django does not own schema migrations — Alembic does. Django manages only its own tables: `auth_*`, `django_session`, `django_content_type`, and `django_admin_log`. This means running `manage.py migrate` is safe: it never touches application tables.

**Auth**: Django has its own user table (`accounts.User`, a custom `AbstractBaseUser`). These are admin-only accounts, separate from the Flask app's session/user system. There is no SSO bridge — admins log in through `/admin/login/`.

**DB connection**: Django reads the same PostgreSQL database via the `PGVECTOR_DSN` environment variable (parsed from DSN format in `settings.py`). No separate DB, no data duplication.

**Deploy**: The `django_admin_deploy()` function in `scripts/deploy.sh` (lines 142–232) handles sync, `migrate`, `collectstatic`, service install, and nginx patching in one shot.

---

## 2. Allan Edwards Stack Compatibility

| Factor | TrueVi | Allan Edwards |
|---|---|---|
| App framework | Flask | Flask |
| ORM | SQLAlchemy | SQLAlchemy |
| Migrations | Alembic | Alembic |
| Database | PostgreSQL (Cloud SQL) | SQLite (`/opt/aedwards/instance/allanedwards.db`) |
| Process manager | systemd gunicorn | systemd gunicorn |
| Reverse proxy | nginx | nginx |
| Current admin | HTMX + custom routes | HTMX + custom routes (users, rejected-emails) |

The same pattern applies verbatim with one simplification: **SQLite instead of PostgreSQL**. The TrueVi settings.py has a `_database_from_dsn()` function to parse a Postgres DSN; Django's SQLite config is just a file path. No Cloud SQL proxy, no credentials, no DSN parsing needed.

**Would Django need duplicate models?**  
Yes — with `managed = False`. Each model is ~5–10 lines mirroring the column names from the Alembic schema. This is the same overhead TrueVi has, and it pays for itself immediately: you get full admin UI per model for free. The alternative (sqladmin or custom routes) requires at least as much code for the form/view layer with no Django-level payoff.

**Migration ownership**  
Alembic stays in charge. Django's `migrate` only touches its own tables. The pattern is proven in TrueVi — no conflicts after many months of production use.

**Models that need CRUD**  
From `src/app/models.py`: `ProductCatalog`, `ProductType`, `PricingTable`, `ShippingConfig`, `Customer`. Secondary candidates: `Contact`, `ShipToAddress`. The `managed = False` mirrors for these are straightforward — no custom foreign-key bridges needed (Django can use `ForeignKey(..., db_constraint=False)` for cross-app relations if desired).

---

## 3. Alternatives

| Option | Effort | UX Quality | Maintenance |
|---|---|---|---|
| Django admin (TrueVi pattern) | Low–Medium (one-time setup ~2–3hrs) | Excellent — proven in prod | Minimal — adding a model is ~15 lines |
| sqladmin | Low (installs into Flask, no new process) | Good but limited — no inline editing, thinner UX | Medium — less community, active dev but smaller |
| Custom HTMX pages | High (each model = full template + routes) | Adequate — matches current admin style | High — every schema change needs template updates |
| Flask-Admin | Low–Medium | Poor — Devin explicitly finds it unsatisfying | Medium |

**sqladmin** (`github.com/aminalaee/sqladmin`) is the closest Django-alternative. It's SQLAlchemy-native and Flask-mountable — no duplicate models, no separate process. Its shortcomings: no inline editing for related models, weaker filtering, thinner UX, and per canon record trueviAI:I202 it ranks below Django admin in information density. For a small internal app it would work, but it doesn't save enough setup time to offset the UX downgrade.

**Custom HTMX pages** are what the current admin uses for users and rejected-emails. Those models have trivial shapes (a few fields, no relations). ProductCatalog and PricingTable are wider and will need sorting, filtering, bulk imports — effort compounds fast.

---

## 4. Recommendation

**Use a separate Django admin instance, following the TrueVi pattern exactly.**

Rationale:

1. **Devin already uses and trusts it in prod.** The pattern is not experimental — it's the same architecture TrueVi has run since task K304/K319. No new concepts to learn.

2. **SQLite makes setup simpler than TrueVi's.** No Cloud SQL proxy, no DSN parsing. Django settings for SQLite is one line: `"NAME": "/opt/aedwards/instance/allanedwards.db"`.

3. **Alembic keeps schema ownership.** `managed = False` on all app models means Django never touches the application tables. The risk of Django accidentally migrating something is zero.

4. **The CRUD payoff is immediate.** ProductCatalog (SKU, description, product_family enum, is_active, timestamps), ProductType (name, display_label, sort_order, is_active), and PricingTable (product_type, key_fields JSON, price) all map cleanly to Django ModelAdmin list views with search and filter at zero additional code.

5. **Deployment already has the nginx scaffolding.** The DO droplet already runs nginx proxying to port 8000. Adding a `/admin` block pointing to port 8001 and a static alias is two nginx locations, matching exactly what `django_admin_deploy()` does in TrueVi's deploy script.

**Setup sketch** (implementation task):
- `admin_django/` directory in repo root: `settings.py`, `urls.py`, `wsgi.py`, `manage.py`
- `admin_django/catalog/`: `models.py` (managed=False mirrors of ProductCatalog, ProductType, PricingTable, ShippingConfig), `admin.py` (ModelAdmin registrations), migrations (only Django auth tables)
- `admin_django/accounts/`: custom User model (or use Django's built-in `auth.User` — simpler since no role complexity)
- `deploy/aedwards-admin.service`: gunicorn on 127.0.0.1:8001
- Nginx update: add `/admin` proxy block + `/admin/static/` alias
- `deploy_web.sh` extension: sync `admin_django/`, run `manage.py migrate`, `collectstatic`, restart service

**What to skip**: Do not bridge Django auth to Flask auth. They're separate users for separate concerns (admin vs. quote tool users). The Django admin login at `/admin/login/` is fine as-is.

Estimated implementation effort: **3–4 hours** for a focused codex agent following the TrueVi pattern as a template.
