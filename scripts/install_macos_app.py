#!/usr/bin/env python3
"""Build our AppleScript app and add one unique entry to the user's Dock."""
import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT = Path(__file__).resolve().parent.parent
APP = Path.home() / "Applications" / "Telegram CRM.app"
BUNDLE_ID = "local.telegram.dialogue-crm"


def applescript_string(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def run():
    APP.parent.mkdir(exist_ok=True)
    if APP.exists():
        info = plistlib.loads((APP / "Contents/Info.plist").read_bytes())
        if info.get("CFBundleIdentifier") != BUNDLE_ID:
            raise RuntimeError("В Applications уже есть другое Telegram CRM.app — оно сохранено.")
    python = PROJECT / ".venv/bin/python"
    service = PROJECT / "scripts/service.py"
    script = f'''on run
    try
        set serviceURL to do shell script (quoted form of {applescript_string(python)} & " " & quoted form of {applescript_string(service)} & " url")
        open location serviceURL
    on error messageText
        display dialog messageText with title "Telegram CRM" buttons {{"OK"}} default button "OK" with icon caution
    end try
end run
'''
    with tempfile.TemporaryDirectory(prefix="telegram-crm-app-") as temporary:
        temporary = Path(temporary)
        source = temporary / "launcher.applescript"
        source.write_text(script)
        built = temporary / "Telegram CRM.app"
        subprocess.run(["/usr/bin/osacompile", "-o", str(built), str(source)], check=True, capture_output=True)
        info_path = built / "Contents/Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        info.update(CFBundleIdentifier=BUNDLE_ID, CFBundleName="Telegram CRM", CFBundleDisplayName="Telegram CRM",
                    CFBundleShortVersionString="0.1.0", NSHighResolutionCapable=True)
        info_path.write_bytes(plistlib.dumps(info))
        icon_path = PROJECT / "assets/TelegramCRM.icns"
        if icon_path.exists():
            shutil.copyfile(icon_path, built / "Contents/Resources/applet.icns")
        if APP.exists():
            shutil.rmtree(APP)
        shutil.copytree(built, APP)
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(APP)], check=True, capture_output=True)
    settings = subprocess.run(["/usr/bin/defaults", "export", "com.apple.dock", "-"], check=True, capture_output=True).stdout
    dock = plistlib.loads(settings)
    existing = dock.get("persistent-apps", [])
    present = any(unquote(urlparse(entry.get("tile-data", {}).get("file-data", {}).get("_CFURLString", "")).path).rstrip('/') == str(APP) for entry in existing)
    if not present:
        entry = {"tile-type":"file-tile", "tile-data":{"file-data":{"_CFURLString":APP.as_uri() + "/", "_CFURLStringType":15},"file-label":"Telegram CRM","bundle-identifier":BUNDLE_ID}}
        xml = plistlib.dumps(entry, fmt=plistlib.FMT_XML).decode()
        subprocess.run(["/usr/bin/defaults", "write", "com.apple.dock", "persistent-apps", "-array-add", xml], check=True)
    subprocess.run(["/usr/bin/killall", "Dock"], capture_output=True)
    print(json.dumps({"application":str(APP),"dock":"already_present" if present else "added"}, ensure_ascii=False))


if __name__ == "__main__":
    if sys.platform != "darwin":
        raise SystemExit("Установщик предназначен для macOS")
    run()

