"""API package for AssetFlow endpoints.

Importing this package automatically registers all API route handlers
from the dedicated modules into the router.
"""
import sys
from pathlib import Path

_deps_dir = str(Path(__file__).resolve().parent.parent)
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

# Import all route modules to register their endpoints
from api import (
    arrivals,
    asset_basic,
    asset_invoice,
    asset_lifecycle,
    asset_purchase,
    asset_vendor,
    assets,
    assignments,
    auth,
    employees,
    maintenance,
    vendors,
    warehouses,
)
