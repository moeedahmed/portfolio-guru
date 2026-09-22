"""Reviewed live effects, independent of the offline coverage classifications."""
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit

COMMAND_POLICY = {name: effect for effect, names in {
    "safe": "help bulk chase cancel",
    "protected": "start privacy settings link unsigned curriculum health arcp upgrade plan gather pathway voice reset delete setup settier setbeta beta listusers filingreport funnelreport assignbeta",
}.items() for name in names.split()}

REVIEWED_EXACT = set("""ACTION|file ACTION|reset ACTION|cancel ACTION|help ACTION|unsigned
ACTION|health ACTION|health_limited ACTION|health_back_to_report ACTION|settings
ACTION|portfolio_defaults ACTION|change_level ACTION|change_pathway ACTION|change_curriculum
ACTION|delete ACTION|refresh_portfolio ACTION|health_review_setup ACTION|back_to_menu
ACTION|back_to_missing ACTION|continue_thin ACTION|retry_recommend ACTION|retry_template
ACTION|same_case_another ACTION|voice INFO|what INFO|stored INFO|stored_after_delete
ACTION|back_to_delete_clear FORM|best FORM|show_all FORM|back FORM|disabled GATHER|done
CANCEL|doc_intent CANCEL|draft CANCEL|edit CANCEL|form CASE|new CASE|improve
AMEND|cancel AMEND|cancel_choice AMEND|start_new AMEND|update_current
DOCUSE|attach DOCUSE|both DOCUSE|ignore DOCUSE|info EDIT|draft
FIELD|clinical_reasoning FIELD|clinical_setting FIELD|curriculum_links FIELD|date_of_encounter
FIELD|patient_presentation FIELD|reflection VOICE|back_to_choice VOICE|back_to_settings
VOICE|more VOICE|cancel VOICE|preview_reject VOICE|path_manual VOICE|path_kaizen UNSIGNED|cancel UNSIGNED|custom
UNSIGNED|3m UNSIGNED|6m UNSIGNED|12m UNSIGNED|all REVIEW|draft IMPROVE|reflection""".split())
PROTECTED = re.compile(
    r"(?:APPROVE\|(?:draft|submit)|CONFIRM\|(?:reset|delete)|UPGRADE\|(?:pro|pro_plus)|"
    r"ACTION\|(?:setup|retry_setup_login|retry_filing|confirm_refresh_portfolio|confirm_refresh_for_health)|"
    r"ACTION\|health_review_confirm\|[^|]+|"
    r"(?:SETLEVEL|SET_CURRICULUM|SETUP_CURRICULUM|PATHWAY|PATHWAY_SETTINGS|CONSENT|"
    r"FILING_CURRICULUM|FEEDBACK|FILING|PUSHBACK|CHASE_LOG|SUP)\|.+|"
    r"VOICE\|(?:done|remove|preview_accept|kaizen_sample\|.+)|ATTACH\|(?:yes|no))"
)
PROTECTED_DYNAMIC = re.compile(
    r"ACTION\|(?:health_view\|(?:about|more|priorities|coverage|actions|scan|legacy_actions)|"
    r"health_queue\|(?:awaiting|draft)\|[0-9]+|health_page\|[0-9]+|"
    r"health_detail\|(?:stuck|domains|basis)|health_review_select\|[0-9]{4}-(?:0[1-9]|1[0-2]))"
)


def command_roots(commands):
    assert set(commands) == set(COMMAND_POLICY), "Unreviewed registered command inventory"
    return sorted(c for c in commands if COMMAND_POLICY[c] == "safe")


def control_policy(payload, url, catalogue):
    if url:
        target = urlsplit(url)
        assert target.scheme == "https" and target.hostname in {
            "checkout.stripe.com", "billing.stripe.com", "kaizenep.com",
            "eportfolio.rcem.ac.uk", "emgurus.com", "www.emgurus.com",
        }, "Unknown URL control"
        return "protected"  # Never open external destinations from this explorer.
    if PROTECTED.fullmatch(payload):
        return "protected"
    # Only static help/toasts are traversed. Conversation reset uses the
    # separately bounded /cancel path, never an arbitrary stateful callback.
    if payload in {"ACTION|help", "FORM|disabled"}:
        return "safe"
    if payload in REVIEWED_EXACT or PROTECTED_DYNAMIC.fullmatch(payload):
        return "protected"
    if payload.startswith("FORM|") and payload[5:] in catalogue.get("forms", []):
        return "protected"
    raise AssertionError("Unknown callback control requires policy review")


def registered_catalogue():
    """Derive the real application in a credential-free, offline child process.

    Only the existing interpreter/path is inherited, never the live environment.
    The child has no Telegram client and cannot load dotenv or persistence.
    """
    code = '''import json
from tests.helpers import build_offline_application
from tests.whole_bot_coverage import Coverage, inventory
import bot
c = Coverage(inventory(build_offline_application()))
print(json.dumps({"commands": sorted({x for s in c.slots for x in s.commands}),
 "digest": c.receipt()["registration"]["digest"],
 "forms": sorted(set(bot.FORM_UUIDS) | {"cat_" + s for s in bot._SLUG_TO_CAT})}))'''
    result = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True,
        text=True, cwd=os.path.dirname(os.path.dirname(__file__)), timeout=30,
        env={"PATH": os.environ.get("PATH", ""), "PYTHON_DOTENV_DISABLED": "1",
             "PYTHONDONTWRITEBYTECODE": "1", "DATABASE_URL": "sqlite://",
             "TELEGRAM_BOT_TOKEN": "0:FAKE", "GOOGLE_API_KEY": "fake",
             "FERNET_SECRET_KEY": "5Wv33F9sq99WGD2lEzwwd3J_JH5p6vxKdDiAwCWqoYQ="})
    catalogue = json.loads(result.stdout)
    command_roots(catalogue["commands"])
    return catalogue


def command_expectation(command):
    return {
        "start": ("ready", "username", "consent", "start"),
        "cancel": ("cancelled", "canceled"), "arcp": ("review date", "arcp"),
        "chase": ("assessor reminders", "coming soon"),
        "voice": ("writing style", "voice"), "link": ("link",),
        "unsigned": ("unsigned", "connect", "unlimited"),
        "plan": ("plan",), "upgrade": ("plan", "upgrade"),
    }.get(command, (command,))
