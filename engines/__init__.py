# Engine modules — 3 engines (Phase C3 overhaul)
# Renamed: smb_maps → companies, ads_intent → ads_running, web_absent → ecommerce
# Removed: social_intent (deleted entirely)
# The old names still work as aliases for backward compatibility with existing tasks.

from engines.smb_maps import run_smb_maps
from engines.ads_intent import run_ads_intent
from engines.ecommerce import run_ecommerce

# Map task_type strings to their engine functions
# Both old and new names map to the same function for backward compatibility
ENGINE_MAP = {
    # New names (primary)
    "companies": run_smb_maps,
    "ads_running": run_ads_intent,
    "ecommerce": run_ecommerce,

    # Old names (backward compatibility for existing tasks in the database)
    "smb_maps": run_smb_maps,
    "ads_intent": run_ads_intent,
    "web_absent": run_ecommerce,  # web_absent tasks now run the ecommerce engine
}
