#!/usr/bin/env python3
"""
Discover Kaizen form UUIDs using browser-use.
Navigates to Kaizen, logs in, and extracts UUIDs for all form types.
"""
import asyncio
import os
import re
import sys
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent))


def load_bws_secrets():
    """Load secrets from Bitwarden Secrets Manager."""
    try:
        from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict
    except ImportError:
        print("bitwarden-sdk not installed. Using environment variables.")
        return

    access_token = os.environ.get("BWS_ACCESS_TOKEN")
    if not access_token:
        print("BWS_ACCESS_TOKEN not set. Using environment variables.")
        return

    client = BitwardenClient(client_settings_from_dict({
        "apiUrl": "https://api.bitwarden.com",
        "identityUrl": "https://identity.bitwarden.com",
        "deviceType": DeviceType.SDK,
        "userAgent": "portfolio-guru",
    }))
    client.access_token_login(access_token)

    # Load secrets
    secrets = {
        "TELEGRAM_BOT_TOKEN": "af553b7d-5c05-418a-b80e-b405015708ed",
        "GOOGLE_API_KEY": "af6579a0-2cbe-4cef-94b3-b405017b48fe",
        "FERNET_SECRET_KEY": "9e653679-9a33-4c23-a15c-b405015713de",
    }

    for env_var, secret_id in secrets.items():
        if not os.environ.get(env_var):
            try:
                secret = client.secrets().get(secret_id)
                os.environ[env_var] = secret.data.value
            except Exception as e:
                print(f"Could not load {env_var}: {e}")


def get_first_credentials():
    """Get first user's credentials from SQLite DB."""
    from cryptography.fernet import Fernet
    from sqlmodel import Session, create_engine, select

    db_path = os.path.expanduser("~/.openclaw/data/portfolio-guru/portfolio_guru.db")
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    fernet_key = os.environ.get("FERNET_SECRET_KEY", "").encode()
    if not fernet_key:
        raise ValueError("FERNET_SECRET_KEY not set")

    f = Fernet(fernet_key)
    engine = create_engine(f"sqlite:///{db_path}")

    # Import model after engine creation
    from credentials import UserCredential

    with Session(engine) as session:
        cred = session.exec(select(UserCredential)).first()
        if not cred:
            raise ValueError("No credentials found in database")
        username = f.decrypt(cred.kaizen_username_enc).decode()
        password = f.decrypt(cred.kaizen_password_enc).decode()
        return username, password


async def discover_form_uuids(username: str, password: str) -> dict:
    """Use browser-use to discover form UUIDs from Kaizen."""
    from browser_use import Agent
    from browser_use.browser import BrowserProfile, BrowserSession
    from browser_use.llm.google.chat import ChatGoogle

    task = f"""
Navigate to Kaizen ePortfolio and discover all assessment form UUIDs.

1. Go to https://eportfolio.rcem.ac.uk and log in with:
   - Username: {username}
   - Password: {password}

2. Wait for the dashboard to load (may take 10-20 seconds).

3. Find and click on "New Assessment" or "New Entry" or any similar button/link that shows assessment options.

4. Look for a list/menu of assessment types. Common types include:
   - CBD (Case-Based Discussion)
   - DOPS (Direct Observation of Procedural Skills)
   - LAT (Leadership Assessment Tool)
   - ACAT (Acute Care Assessment Tool)
   - mini-CEX
   - QIPAT
   - MSF (Multi-Source Feedback)
   - PS (Personal Statement)
   - STAT

5. For EACH assessment type you find:
   - Hover over or click the link
   - Extract the UUID from the URL or href attribute
   - The URL format is: https://kaizenep.com/events/new-section/UUID
   - UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

6. Report ALL discovered UUIDs in this exact format:
   FORM_UUID|FormName|uuid-here

For example:
FORM_UUID|CBD|3ce5989a-b61c-4c24-ab12-711bf928b181
FORM_UUID|DOPS|xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

Extract as many UUIDs as you can find. Look at all links and buttons carefully.
"""

    browser_profile = BrowserProfile(headless=True)
    browser_session = BrowserSession(browser_profile=browser_profile)

    llm = ChatGoogle(
        model="gemini-3-flash-preview",
        api_key=os.environ.get("GOOGLE_API_KEY"),
    )

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=browser_session,
        step_timeout=120,
        max_steps=40,
    )

    try:
        result = await agent.run()
        result_str = str(result)

        # Parse discovered UUIDs
        uuids = {}
        uuid_pattern = r"FORM_UUID\|([^|]+)\|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        matches = re.findall(uuid_pattern, result_str, re.IGNORECASE)

        for form_name, uuid in matches:
            form_name = form_name.strip().upper()
            # Normalize form names
            if "CBD" in form_name:
                form_name = "CBD"
            elif "DOPS" in form_name:
                form_name = "DOPS"
            elif "LAT" in form_name:
                form_name = "LAT"
            elif "ACAT" in form_name:
                form_name = "ACAT"
            elif "STAT" in form_name:
                form_name = "STAT"
            elif "MINI" in form_name or "CEX" in form_name:
                form_name = "mini-CEX"
            elif "QIPAT" in form_name:
                form_name = "QIPAT"
            elif "MSF" in form_name:
                form_name = "MSF"

            uuids[form_name] = uuid.lower()

        # Also try to extract UUIDs from URLs in the result
        url_pattern = r"kaizenep\.com/events/new-section/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        url_matches = re.findall(url_pattern, result_str, re.IGNORECASE)
        for uuid in url_matches:
            if uuid.lower() not in uuids.values():
                # Unknown form type, add as generic
                uuids[f"UNKNOWN_{len(uuids)}"] = uuid.lower()

        return uuids

    finally:
        try:
            await browser_session.close()
        except Exception:
            pass


def update_extractor_uuids(discovered: dict):
    """Update FORM_UUIDS in extractor.py with discovered values."""
    extractor_path = Path(__file__).parent / "extractor.py"
    content = extractor_path.read_text()

    # Known UUID that should not be overwritten
    known_uuids = {
        "CBD": "3ce5989a-b61c-4c24-ab12-711bf928b181",
    }

    # Build new FORM_UUIDS dict
    all_forms = ["CBD", "ACAT", "DOPS", "LAT", "STAT"]
    for form in discovered:
        if form not in all_forms and not form.startswith("UNKNOWN"):
            all_forms.append(form)

    new_dict_lines = ["FORM_UUIDS = {"]
    for form in all_forms:
        if form in known_uuids:
            uuid = known_uuids[form]
            new_dict_lines.append(f'    "{form}":  "{uuid}",')
        elif form in discovered:
            uuid = discovered[form]
            new_dict_lines.append(f'    "{form}": "{uuid}",  # discovered')
        else:
            new_dict_lines.append(f'    "{form}": None,  # TODO: verify UUID from Kaizen')
    new_dict_lines.append("}")

    new_dict = "\n".join(new_dict_lines)

    # Replace old FORM_UUIDS
    pattern = r"FORM_UUIDS = \{[^}]+\}"
    new_content = re.sub(pattern, new_dict, content)

    if new_content != content:
        extractor_path.write_text(new_content)
        print(f"Updated {extractor_path}")
    else:
        print("No changes made to extractor.py")


async def main():
    print("Loading secrets...")
    load_bws_secrets()

    print("Loading credentials from database...")
    try:
        username, password = get_first_credentials()
        print(f"Loaded credentials for: {username}")
    except Exception as e:
        print(f"Error loading credentials: {e}")
        sys.exit(1)

    print("Discovering form UUIDs from Kaizen...")
    try:
        discovered = await discover_form_uuids(username, password)
    except Exception as e:
        print(f"Error discovering UUIDs: {e}")
        sys.exit(1)

    # Print results
    print("\n" + "=" * 50)
    print("DISCOVERED FORM UUIDs:")
    print("=" * 50)

    # Include known CBD UUID
    all_uuids = {"CBD": "3ce5989a-b61c-4c24-ab12-711bf928b181"}
    all_uuids.update(discovered)

    print("\nFORM_UUIDS = {")
    for form, uuid in sorted(all_uuids.items()):
        if uuid:
            print(f'    "{form}": "{uuid}",')
        else:
            print(f'    "{form}": None,')
    print("}")

    # Update extractor.py
    print("\nUpdating extractor.py...")
    update_extractor_uuids(discovered)

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
