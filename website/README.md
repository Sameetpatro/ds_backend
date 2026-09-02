# Humsafar Web Fallback & App Links Handoff

This directory documents the contract for the separate React / Vercel fallback landing page.

---

## 1. Domain Configuration

The Android application associates with the public domain (e.g. `humsafar.vercel.app` or your custom domain).

The web server MUST serve the following file at:
```text
https://<domain>/.well-known/assetlinks.json
```
* **Content-Type:** `application/json`
* **Access-Control-Allow-Origin:** `*`

### `assetlinks.json` Content
```json
[
  {
    "relation": ["delegate_permission/common.handle_all_urls"],
    "target": {
      "namespace": "android_app",
      "package_name": "com.example.humsafar",
      "sha256_cert_fingerprints": [
        "7F:47:04:22:EF:98:91:A0:7B:DA:41:84:C6:E8:F8:8A:C5:FD:DC:A1:EF:2D:07:2B:A3:A1:1A:45:16:AD:7E:88"
      ]
    }
  }
]
```

> **Note on Production Fingerprint:**
> When publishing to Google Play with Google Play App Signing, replace or append the production app signing key SHA-256 fingerprint retrieved from:
> **Google Play Console → Setup → App integrity → App signing key certificate → SHA-256 certificate fingerprint**.

---

## 2. Web Route Contract

### Route
```text
GET /node/:nodeId
```

### Example URL
```text
https://<domain>/node/NODE_1
https://<domain>/node/IIITS-0-KING
```

### Behavior & UX Guidelines
* **Target Audience:** Users scanning a QR code without the Android app installed.
* **Page Purpose:** Application download landing page only.
* **DO NOT:**
  * Do NOT fetch node details or call the node backend API.
  * Do NOT build an alternative web-based audio tour or chat system.
* **DO:**
  * Display a clean, responsive landing page explaining the **Dharohar Setu / Humsafar** experience.
  * Provide a prominent Call to Action (CTA) linking to the Google Play Store / APK download.
  * Optionally display the scanned spot code (e.g. `Spot: NODE_1`) with a message: *"Download the Android app to unlock the AI tour guide, audio stories, and live navigation for this spot!"*
