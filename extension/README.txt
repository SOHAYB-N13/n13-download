Terminal Download Manager - Chrome Extension
==========================================

1. Start TDM and run Live Server (menu option 7 → 6)
2. Copy extension folder or use "Create Chrome extension copy" in TDM
3. Load unpacked in chrome://extensions/
4. Ensure token.json is present (generated when copying extension)

The extension only talks to http://127.0.0.1:6868 with a bearer token.
Protocol handler (dldm://) is used as fallback on supported platforms.
