# Engine modules
from engines.ads_intent import run_ads_intent
from engines.smb_maps import run_smb_maps
from engines.web_absent import run_web_absent
from engines.social_intent import run_social_intent

ENGINE_MAP = {
    "ads_intent": run_ads_intent,
    "smb_maps": run_smb_maps,
    "web_absent": run_web_absent,
    "social_intent": run_social_intent,
}
