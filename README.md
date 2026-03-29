# DocScan — Phone-to-Desktop Document Scanner

Scan documents with your phone camera and instantly see the processed result on your desktop.
Uses the [image2scan](https://github.com/Manu10744/image2scan) engine (enhanced) for edge detection, perspective correction, and clean scan output.

## Architecture

```
┌──────────────┐         WebSocket          ┌──────────────┐
│   Desktop    │◄──────────────────────────►│    Server     │
│   Browser    │   scan results, status     │  (Python)     │
│              │                            │               │
│  • QR Code   │                            │  • Session    │
│  • Preview   │                            │    Manager    │
│  • Download  │                            │  • Scanner    │
└──────────────┘                            │    Engine     │
                                            └───────┬───────┘
                                                    │ WebSocket
┌──────────────┐                                    │
│   Mobile     │◄───────────────────────────────────┘
│   Browser    │   image upload, status
│              │
│  • Camera    │
│  • Gallery   │
│  • Preview   │
└──────────────┘
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the server
python server.py

# 3. Open the desktop dashboard in your browser
#    → http://<your-local-ip>:8765/

# 4. Scan the QR code with your phone
#    (both devices must be on the same Wi-Fi network)

# 5. Capture a document photo on your phone
#    → The scanned result appears on your desktop instantly
```

## Features

- **QR Code Pairing**: Scan from desktop to instantly connect your phone
- **Real-time WebSocket**: Images transfer and appear on desktop within seconds
- **Smart Edge Detection**: 5 different detection strategies for robust document finding
- **3 Scan Modes**:
  - **B&W Scan**: Clean black & white with adaptive thresholding
  - **Color Enhanced**: Denoised + contrast-enhanced color output
  - **Original Crop**: Perspective-corrected crop without filters
- **PDF Export**: Download scanned documents as A4 PDFs
- **Scan History**: Browse all captured documents in the session
- **Re-scan**: Switch between modes without re-uploading

## Scanner Engine

Enhanced from `Manu10744/image2scan` with:

| Feature | Original | Enhanced |
|---------|----------|----------|
| Operation mode | GUI (cv2.imshow) | Headless (server-ready) |
| Edge detection | Single strategy | 5 cascading strategies |
| Output format | File on disk | Base64 + PDF (WebSocket-ready) |
| Color enhancement | None | CLAHE + denoising + sharpening |
| Brightness correction | None | Auto histogram-based |
| Fallback on no edges | Manual point selection (GUI) | Returns enhanced original |

## Project Structure

```
docscanner/
├── server.py           # HTTP + WebSocket server (aiohttp)
├── scanner.py          # Enhanced DocumentScanner engine
├── requirements.txt
├── static/
│   ├── desktop.html    # Desktop dashboard UI
│   └── mobile.html     # Mobile capture page
├── uploads/            # Original uploaded images
└── scans/              # Processed scan results
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Desktop dashboard |
| GET | `/mobile?session=<id>` | Mobile capture page |
| GET | `/ws?session=<id>&role=<desktop\|mobile>` | WebSocket endpoint |
| POST | `/api/session` | Create new session, returns QR code |
| GET | `/api/health` | Server health check |

## Network Requirements

Both desktop and phone must be connected to the **same local network** (Wi-Fi).
The server binds to `0.0.0.0:8765` so it's accessible from any device on the network.
