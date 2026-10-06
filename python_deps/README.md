# AssetFlow sign-in backend

The site must be opened through the Python server so that sign-in, signup, and logout can use the MySQL-backed session API.

From the `fixed_asset` folder, with Python 3.10 or newer and the MySQL service running:

```powershell
python -m pip install -r python_deps/requirements.txt
python python_deps/backend.py
```

Then open `http://127.0.0.1:8000`. The server uses the existing `fixed_asset.user_login` table and does not create or alter columns. New accounts save the name, email, password hash, user role, and existing table defaults. Passwords are never stored in browser storage. The admin role is read from MySQL.

The existing table has no organization, birth-date, phone, or asset-data columns. Account creation therefore asks only for fields the table can store; asset and vendor records continue using the website's existing frontend storage.

Database connection values can be overridden with `ASSETFLOW_DB_HOST`, `ASSETFLOW_DB_USER`, `ASSETFLOW_DB_PASSWORD`, and `ASSETFLOW_DB_NAME` environment variables. The backend code and default local database settings are kept in this folder.
