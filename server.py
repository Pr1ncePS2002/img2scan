"""
DocScan Server
==============
Serves the desktop dashboard + mobile capture page.
Uses WebSocket for real-time image transfer between phone and desktop.
Integrates the enhanced DocumentScanner for processing.

Architecture:
  Desktop Browser ◄──WebSocket──► Server ◄──WebSocket──► Mobile Browser
                                    │
                              DocumentScanner
                           (edge detect, crop,
                            perspective fix, scan)
"""

import asyncio
import json
import os
import sys
import uuid
import socket
import base64
import logging
import qrcode
import io
from datetime import datetime
from aiohttp import web
import aiohttp

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scanner import DocumentScanner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DocScanServer")

# ─── Configuration ───────────────────────────────────────────────────────────

HOST = "0.0.0.0"
PORT = 8765
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
SCAN_DIR = os.path.join(os.path.dirname(__file__), "scans")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(SCAN_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# ─── State ───────────────────────────────────────────────────────────────────

scanner = DocumentScanner()

# Session management: session_id -> {desktop_ws, mobile_ws, images[]}
sessions = {}


def get_local_ip():
    """Get the machine's local network IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def generate_qr_b64(url):
    """Generate QR code as base64 PNG."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4,
                        error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('utf-8')


# ─── WebSocket Handler ──────────────────────────────────────────────────────

async def websocket_handler(request):
    ws = web.WebSocketResponse(max_msg_size=50 * 1024 * 1024)  # 50MB limit
    await ws.prepare(request)

    session_id = request.query.get("session")
    role = request.query.get("role", "desktop")

    if not session_id:
        await ws.close(message=b"Missing session ID")
        return ws

    # Register connection
    if session_id not in sessions:
        sessions[session_id] = {"desktop": None, "mobile": None, "images": []}

    session = sessions[session_id]
    session[role] = ws

    logger.info(f"[{role.upper()}] Connected to session {session_id[:8]}...")

    # Notify desktop when mobile connects
    if role == "mobile" and session.get("desktop") and not session["desktop"].closed:
        await session["desktop"].send_json({
            "type": "mobile_connected",
            "message": "Mobile device connected!"
        })

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                await handle_message(session_id, role, data)

            elif msg.type == aiohttp.WSMsgType.BINARY:
                # Binary image data from mobile
                await handle_binary_image(session_id, msg.data)

            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                break

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        session[role] = None
        # Notify other side
        other = "desktop" if role == "mobile" else "mobile"
        other_ws = session.get(other)
        if other_ws and not other_ws.closed:
            await other_ws.send_json({
                "type": f"{role}_disconnected",
                "message": f"{role.title()} device disconnected"
            })
        logger.info(f"[{role.upper()}] Disconnected from session {session_id[:8]}")

    return ws


async def handle_message(session_id, role, data):
    """Handle JSON messages."""
    session = sessions.get(session_id)
    if not session:
        return

    msg_type = data.get("type")

    if msg_type == "image_upload":
        # Image sent as base64 from mobile
        image_b64 = data.get("image", "")
        filename = data.get("filename", "capture.jpg")

        # Notify desktop: image received, processing...
        desktop_ws = session.get("desktop")
        if desktop_ws and not desktop_ws.closed:
            await desktop_ws.send_json({
                "type": "image_received",
                "message": "Image received from mobile, showing preview...",
                "preview_b64": image_b64[:200000]  # Send preview
            })

        # Decode and process
        image_bytes = base64.b64decode(image_b64)

        # Save original
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_path = os.path.join(UPLOAD_DIR, f"{timestamp}_{filename}")
        with open(original_path, "wb") as f:
            f.write(image_bytes)

        # Notify desktop: processing started
        if desktop_ws and not desktop_ws.closed:
            await desktop_ws.send_json({
                "type": "processing_started",
                "message": "Running document detection & scanning..."
            })

        # Run scanner in thread pool to not block event loop
        loop = asyncio.get_event_loop()
        corners = data.get("corners")  # Manual corners from mobile

        if corners and len(corners) == 4:
            # User selected corners on mobile — use them directly
            result = await loop.run_in_executor(
                None, scanner.scan_with_manual_corners, image_bytes, corners, "original"
            )
        else:
            # Auto-detect edges
            result = await loop.run_in_executor(
                None, scanner.scan_image_from_bytes, image_bytes, "original"
            )

        # Send result to desktop
        if desktop_ws and not desktop_ws.closed:
            await desktop_ws.send_json({
                "type": "scan_result",
                "success": result["success"],
                "message": result["message"],
                "original_b64": image_b64,
                "scanned_b64": result.get("image_b64"),
                "outlined_b64": result.get("outlined_b64"),
                "pdf_b64": result.get("pdf_b64"),
                "corners": corners if corners else result.get("corners"),
                "timestamp": timestamp
            })

        # Save scanned result
        if result.get("image_b64"):
            scan_bytes = base64.b64decode(result["image_b64"])
            scan_path = os.path.join(SCAN_DIR, f"{timestamp}_scanned.jpg")
            with open(scan_path, "wb") as f:
                f.write(scan_bytes)

        # Confirm to mobile
        mobile_ws = session.get("mobile")
        if mobile_ws and not mobile_ws.closed:
            await mobile_ws.send_json({
                "type": "upload_confirmed",
                "success": result["success"],
                "message": result["message"]
            })

    elif msg_type == "rescan":
        image_b64 = data.get("image")
        corners = data.get("corners")
        if image_b64:
            image_bytes = base64.b64decode(image_b64)
            loop = asyncio.get_event_loop()

            if corners and len(corners) == 4:
                result = await loop.run_in_executor(
                    None, scanner.scan_with_manual_corners, image_bytes, corners, "original"
                )
            else:
                result = await loop.run_in_executor(
                    None, scanner.scan_image_from_bytes, image_bytes, "original"
                )

            desktop_ws = session.get("desktop")
            if desktop_ws and not desktop_ws.closed:
                await desktop_ws.send_json({
                    "type": "rescan_result",
                    "success": result["success"],
                    "message": result["message"],
                    "scanned_b64": result.get("image_b64"),
                    "pdf_b64": result.get("pdf_b64")
                })

    elif msg_type == "ping":
        # Keepalive
        target = session.get(role)
        if target and not target.closed:
            await target.send_json({"type": "pong"})


async def handle_binary_image(session_id, image_bytes):
    """Handle binary image upload from mobile."""
    session = sessions.get(session_id)
    if not session:
        return

    desktop_ws = session.get("desktop")
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')

    if desktop_ws and not desktop_ws.closed:
        await desktop_ws.send_json({
            "type": "processing_started",
            "message": "Processing captured image..."
        })

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, scanner.scan_image_from_bytes, image_bytes, "scan"
    )

    if desktop_ws and not desktop_ws.closed:
        await desktop_ws.send_json({
            "type": "scan_result",
            "success": result["success"],
            "message": result["message"],
            "original_b64": image_b64,
            "scanned_b64": result.get("image_b64"),
            "outlined_b64": result.get("outlined_b64"),
            "pdf_b64": result.get("pdf_b64"),
            "corners": result.get("corners"),
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S")
        })


# ─── HTTP Routes ─────────────────────────────────────────────────────────────

async def index_handler(request):
    """Serve the desktop dashboard."""
    return web.FileResponse(os.path.join(STATIC_DIR, "desktop.html"))


async def mobile_handler(request):
    """Serve the mobile capture page."""
    return web.FileResponse(os.path.join(STATIC_DIR, "mobile.html"))


async def create_session_handler(request):
    """Create a new scanning session and return QR code."""
    session_id = str(uuid.uuid4())
    sessions[session_id] = {"desktop": None, "mobile": None, "images": []}

    local_ip = get_local_ip()
    mobile_url = f"http://{local_ip}:{PORT}/mobile?session={session_id}"
    qr_b64 = generate_qr_b64(mobile_url)

    return web.json_response({
        "session_id": session_id,
        "mobile_url": mobile_url,
        "qr_code": qr_b64
    })


async def health_handler(request):
    return web.json_response({"status": "ok", "sessions": len(sessions)})


# ─── App Setup ───────────────────────────────────────────────────────────────

def create_app():
    app = web.Application(client_max_size=50 * 1024 * 1024)  # 50MB

    # Routes
    app.router.add_get("/", index_handler)
    app.router.add_get("/mobile", mobile_handler)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_post("/api/session", create_session_handler)
    app.router.add_get("/api/health", health_handler)
    app.router.add_static("/static/", STATIC_DIR, name="static")

    return app


if __name__ == "__main__":
    local_ip = get_local_ip()
    logger.info(f"Starting DocScan Server on http://{local_ip}:{PORT}")
    logger.info(f"Desktop dashboard: http://{local_ip}:{PORT}/")
    app = create_app()
    web.run_app(app, host=HOST, port=PORT, print=None)
