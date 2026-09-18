# EasyOats Order Manager

An Arabic, right-to-left order manager for EasyOats. The database is the source of truth—SQLite locally and PostgreSQL when hosted—and the supplied Excel workbook is a synchronized operational report. The interface covers orders, customer history, delivery, collection, inventory, feedback, and export.

The project also includes a production multi-user path using managed PostgreSQL, Google/Microsoft OIDC sign-in, staff/admin roles, versioned migrations, and durable hosted Excel storage. Follow [DEPLOYMENT.md](DEPLOYMENT.md) for the exact cutover procedure.

## Start on Windows

**Double-click `run.bat` in this folder.** It prepares the virtual environment, installs dependencies, and opens the app at **http://localhost:8501**. Keep its console window open while working. Press **Ctrl+C** in that window to stop the app.

The environment in this workspace has already been prepared with Python 3.12. On a different computer, install Python 3.12 first and keep `EasyOats_Order_Tracker.xlsx` beside `app.py`. The first launch needs internet access to install packages. Subsequent launches reuse the environment.

From PowerShell in this folder, the equivalent command is:

```powershell
.\run.bat
```

For development with an existing Python 3.12 installation:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe scripts\upgrade_database.py
.\.venv\Scripts\python.exe -m streamlit run app.py
```

The local server binds to `127.0.0.1` by default. Hosted mode uses OIDC accounts and PostgreSQL as described in the deployment guide.

## Daily use

1. Select the current operator in **الإعدادات والتصدير** so changes have an audit name.
2. Use **طلب جديد** to create an order. The app assigns a permanent ID such as `EO-000001` and reserves the quantities.
3. Use **البحث عن عميل** to search an Egyptian mobile number. Local, `+20`, `0020`, Arabic numerals, spaces, dashes, and brackets normalize to the same number. Exact search is the default; optional partial search requires at least four digits.
4. Open a result to update delivery, collection, feedback, or notes. Delivered orders require their actual delivery date. Cancellation and returns retain the order and its history.
5. Record additions and physical counts in **المخزون**. Physical counts are observations, not automatic stock adjustments. Reserved units are still physically on hand until delivery.
6. Record or edit feedback and issue follow-up in **الفيدباك**. Store the existing Google Form URL in settings and copy it from the interface. No Google Form API connection is used.

Administrators can add a flavor or product from **المخزون → إضافة نكهة / منتج جديد**. Each product has a stable English SKU, Arabic display name, opening stock, unit cost, and reorder level. Active products appear automatically in order forms. A product can be stopped for new orders without removing it from historical orders.

All successful changes are committed to the database before export. When Excel is open and blocks saving, the order is still saved. Close Excel and click **إعادة محاولة مزامنة Excel**. Settings also offers an immediate full export, the last successful sync time, and a workbook download.

Administrators can also update existing orders through Excel. Download the latest workbook from **الإعدادات والتصدير → Excel والتصدير**, edit rows in **الطلبات**, and upload the workbook in the import section. The app validates the file, rejects stale copies and changed order IDs, previews every change, requires an audit reason, and applies the approved changes in one transaction. New orders, inventory adjustments, settings, and calculated columns must be managed in the app.

## Data, workbook preservation, and recovery

- Default database: `data/easyoats.db`. Workbook edits affect application records only through the administrator import flow. Directly replacing the operational workbook does not update the database.
- Operational report: `EasyOats_Order_Tracker.xlsx`.
- Before its first write, the exporter preserves an untouched original in `backups/original_template.xlsx`. Every export also creates a timestamped backup before replacing the report.
- The exporter saves in the workbook directory, then atomically replaces the destination. An export lock serializes concurrent exports. A failed export leaves the prior workbook intact and persists pending status for retry.
- Existing sheet names, dashboard/chart, styles, formulas, and dropdowns are retained. Calculations are corrected where needed for the specified rules. Extra report columns retain normalized phones, separate location links, frozen pricing/costs, and return/refund data. Table ranges, validation, formatting, and dependent formulas grow beyond 200 records.
- The supplied demonstration order and its feedback are excluded. The original template retains them for reference.
- Excel is an operational backup, not a replacement for the database's full audit history. For a complete backup, stop the application and copy the entire `data/` folder, workbook, and `backups/` folder to a safe location. Stop the application before restoring a matching copy. If using SQLite while the app is running, do not copy only the `.db` file because committed data may still be in its WAL file.
- Generated workbooks, databases, backups, local secrets, logs, and temporary verification files are excluded from Git. Keep the original workbook with any distributed copy of this project.

## Calculation and workflow decisions

- Initial prices: standard EGP 75 per cup; two-cup offer EGP 120 (EGP 60 per cup); sample product price zero. The offer applies EGP 60 to each cup, including odd quantities, as requested.
- Initial unit costs: EGP 44 for both supplied flavors. Each added product has its own unit cost managed from the inventory page.
- Initial stock: 238 honey and milk, 250 date molasses and milk; reorder point 50 each.
- Each order stores its price and product-cost snapshots. Changing settings affects new orders, while historical orders retain their agreed economics.
- Total due = products − discount + customer delivery fee. Product cost = each product quantity × its frozen unit cost. Contribution = total due − product cost − actual delivery cost. Outstanding = total due − amount collected. Overpayment appears as a negative outstanding amount.
- An explicit refund amount distinguishes a returned order from an actually refunded payment. Original amount collected is retained; a fully refunded collected payment displays **مسترد**. Outstanding follows the specified gross formula; refunds are recorded separately.
- Active orders reserve stock. Delivered units reduce physical on-hand stock. Cancelled orders release reservations. Returned units remain unavailable unless the operator explicitly confirms they are sellable. Previously delivered or returned orders cannot be cancelled to bypass this confirmation.
- Negative stock is blocked unless the operator authenticates as administrator and supplies an audit reason. The override is disabled until `ADMIN_PIN` is configured.
- Normal orders require at least one cup. Special/manual **أخرى** orders can represent a zero-unit transaction.
- Dashboard revenue, units, average order value, and contribution exclude cancelled/returned orders. Collected revenue is net of recorded refunds; the collection queue sums positive outstanding amounts on active/delivered orders. Each order still shows its exact signed balance. Ratings use the current order summaries, and feedback response rate counts responding delivered orders once.

## Configuration

Copy `.env.example` to `.env` if configuration is needed. The default configuration works without an `.env` file.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Empty uses SQLite. PostgreSQL example: `postgresql+psycopg://user:password@localhost/easyoats`. |
| `EXCEL_PATH` | Path to the supplied operational workbook. |
| `BACKUP_DIR` | Workbook backup directory. |
| `ADMIN_PIN` | Private PIN for authorized negative-stock overrides; empty disables overrides. |

Hosted deployment also uses `AUTH_REQUIRED`, the `OIDC_*` variables, `AUTH_COOKIE_SECRET`, `BOOTSTRAP_ADMIN_EMAILS`, `BOOTSTRAP_STAFF_EMAILS`, and `TEMPLATE_PATH`; see `DEPLOYMENT.md`. In hosted mode, the verified admin role replaces the shared PIN.

PostgreSQL uses the same SQLAlchemy models and services. Use `scripts/migrate_sqlite_to_postgres.py` for a verified one-time transfer; changing the URL alone does not copy records.

## Tests and verification

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests cover phone normalization/search, pricing and payments, stock reservation/cancellation/returns, unique concurrent order creation, audit records, validation, feedback, workbook formulas and values, guarded Excel import, backups, atomic failures, and export capacity beyond 200 orders. Tests use isolated temporary databases and workbook copies.

`requirements.txt` lists supported direct dependencies; `requirements-lock.txt` records the installed, verified environment. Streamlit's official [AppTest documentation](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest) describes the UI test runner used by this project.

## Project structure

```text
EasyOats_Order_App/
├── app.py                     # Arabic Streamlit entry point
├── constants.py               # Arabic domain labels and initial settings
├── database.py                # SQLAlchemy connection and transactions
├── models.py                  # Orders, line items, products, feedback, settings, audit, sync
├── pages/                     # Complete interface pages and shared UI
├── assets/                    # RTL responsive styling
├── services/
│   ├── order_service.py       # Application operations, validation and audit
│   ├── inventory_service.py   # Inventory projection
│   ├── excel_service.py       # Template-preserving atomic export
│   └── excel_import_service.py # Validated, revision-bound order import
├── utils/
│   └── phone.py               # Egyptian number normalization
├── tests/                     # Automated functional and integration tests
├── data/                      # Local database (ignored)
├── backups/                   # Original and timestamped workbooks (ignored)
├── .streamlit/config.toml     # Theme and localhost configuration
├── .env.example
├── .gitignore
├── pytest.ini
├── requirements.txt
├── requirements-lock.txt
├── run.bat
├── README.md
├── logo.png
└── EasyOats_Order_Tracker.xlsx # Supplied operational report (ignored)
```
