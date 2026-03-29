"""
Enhanced Document Scanner - adapted from image2scan (Manu10744/image2scan)
Adds: headless operation, enhanced preprocessing, JSON-friendly output,
      brightness/contrast correction, and multiple output formats.
"""

import cv2
import imutils
import logging
import numpy as np
import os
import img2pdf
import base64
from skimage.filters import threshold_local
from datetime import datetime

logger = logging.getLogger("SCANNER")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(name)s | [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


class DocumentScanner:
    """
    Enhanced document scanner that detects rectangular documents in images,
    applies perspective correction, and produces clean scanned output.
    
    Improvements over base image2scan:
    - Headless mode (no GUI popups) for server use
    - Returns base64-encoded results for WebSocket transmission
    - Multiple output modes: grayscale scan, color corrected, original crop
    - Enhanced edge detection with multiple preprocessing strategies
    - Auto brightness/contrast adjustment
    """

    def __init__(self):
        pass

    def scan_image_from_bytes(self, image_bytes, mode="scan"):
        """
        Process raw image bytes and return scanned document.
        
        Args:
            image_bytes: Raw image file bytes
            mode: 'scan' (B&W scan), 'color' (color corrected), 'original' (just crop)
            
        Returns:
            dict with keys:
                - success: bool
                - image_b64: base64 encoded result image (JPEG)
                - pdf_b64: base64 encoded PDF (optional)
                - corners: detected corner points
                - message: status message
        """
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if image is None:
            return {"success": False, "message": "Failed to decode image", "image_b64": None}

        return self._process_image(image, mode)

    def scan_with_manual_corners(self, image_bytes, corners, mode="scan"):
        """
        Process image using manually specified corner points.

        Args:
            image_bytes: Raw image file bytes
            corners: List of 4 [x, y] points (in original image coordinates)
            mode: 'scan', 'color', or 'original'

        Returns:
            dict with success, image_b64, pdf_b64, message
        """
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if image is None:
            return {"success": False, "message": "Failed to decode image", "image_b64": None}

        result = {
            "success": False,
            "image_b64": None,
            "pdf_b64": None,
            "message": ""
        }

        try:
            pts = np.array(corners, dtype="float32")
            transformed = self._four_point_transform(image, pts)

            if mode == "scan":
                gray = cv2.cvtColor(transformed, cv2.COLOR_BGR2GRAY)
                gray = self._auto_brightness_contrast(gray)
                T = threshold_local(gray, 11, offset=10, method="gaussian")
                scan_result = (gray > T).astype("uint8") * 255
            elif mode == "color":
                scan_result = self._enhance_color(transformed)
            else:
                scan_result = transformed

            _, img_buf = cv2.imencode('.jpg', scan_result, [cv2.IMWRITE_JPEG_QUALITY, 92])
            result["image_b64"] = base64.b64encode(img_buf).decode('utf-8')
            result["pdf_b64"] = self._generate_pdf_b64(scan_result)
            result["success"] = True
            result["message"] = "Document cropped with manual corners"
            logger.info("Manual corner crop completed successfully")

        except Exception as e:
            logger.error(f"Manual crop error: {e}")
            result["message"] = f"Manual crop error: {str(e)}"

        return result

    def scan_image_from_path(self, image_path, mode="scan"):
        """Process image from file path."""
        image = cv2.imread(image_path)
        if image is None:
            return {"success": False, "message": f"Failed to read image: {image_path}", "image_b64": None}
        return self._process_image(image, mode)

    def _process_image(self, original_image, mode="scan"):
        """Core processing pipeline."""
        result = {
            "success": False,
            "image_b64": None,
            "outlined_b64": None,
            "pdf_b64": None,
            "corners": None,
            "message": ""
        }

        try:
            # Resize for contour detection (keep original for final transform)
            resized = imutils.resize(original_image, height=500)
            ratio = original_image.shape[0] / 500.0

            # Try multiple edge detection strategies
            contour = self._detect_document_contour(resized)

            if contour is not None:
                # Draw outlined preview
                outlined = resized.copy()
                cv2.drawContours(outlined, [contour], -1, (0, 255, 0), 2)

                # Mark corners
                for point in contour.reshape(-1, 2):
                    cv2.circle(outlined, tuple(point), 6, (0, 0, 255), -1)

                _, outlined_buf = cv2.imencode('.jpg', outlined, [cv2.IMWRITE_JPEG_QUALITY, 85])
                result["outlined_b64"] = base64.b64encode(outlined_buf).decode('utf-8')
                result["corners"] = contour.reshape(4, 2).tolist()

                # Transform using original resolution
                scaled_contour = contour.reshape(4, 2) * ratio
                transformed = self._four_point_transform(original_image, scaled_contour)

                if mode == "scan":
                    # Apply adaptive thresholding for clean B&W scan
                    gray = cv2.cvtColor(transformed, cv2.COLOR_BGR2GRAY)
                    # Enhance contrast before thresholding
                    gray = self._auto_brightness_contrast(gray)
                    T = threshold_local(gray, 11, offset=10, method="gaussian")
                    scan_result = (gray > T).astype("uint8") * 255
                elif mode == "color":
                    # Color corrected version
                    scan_result = self._enhance_color(transformed)
                else:
                    # Original crop
                    scan_result = transformed

                # Encode result
                _, img_buf = cv2.imencode('.jpg', scan_result, [cv2.IMWRITE_JPEG_QUALITY, 92])
                result["image_b64"] = base64.b64encode(img_buf).decode('utf-8')

                # Generate PDF
                result["pdf_b64"] = self._generate_pdf_b64(scan_result)

                result["success"] = True
                result["message"] = "Document detected and scanned successfully"
            else:
                # No document edges found - return enhanced original
                logger.warning("Could not detect document edges. Returning enhanced image.")
                enhanced = self._enhance_color(original_image)
                _, img_buf = cv2.imencode('.jpg', enhanced, [cv2.IMWRITE_JPEG_QUALITY, 92])
                result["image_b64"] = base64.b64encode(img_buf).decode('utf-8')
                result["success"] = True
                result["message"] = "No document edges detected. Returned enhanced original image."

        except Exception as e:
            logger.error(f"Scan error: {e}")
            result["message"] = f"Processing error: {str(e)}"

        return result

    def _detect_document_contour(self, image):
        """
        Try multiple strategies to detect document edges.
        Returns the best 4-point contour found, or None.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        strategies = [
            # Strategy 1: Standard Canny
            lambda g: cv2.Canny(cv2.GaussianBlur(g, (5, 5), 0), 75, 200),
            # Strategy 2: Wider Canny range
            lambda g: cv2.Canny(cv2.GaussianBlur(g, (5, 5), 0), 50, 150),
            # Strategy 3: Bilateral filter + Canny (better for noisy images)
            lambda g: cv2.Canny(cv2.bilateralFilter(g, 9, 75, 75), 60, 180),
            # Strategy 4: Morphological + Canny
            lambda g: cv2.Canny(
                cv2.morphologyEx(cv2.GaussianBlur(g, (5, 5), 0), cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))),
                50, 200
            ),
            # Strategy 5: Adaptive threshold based
            lambda g: cv2.adaptiveThreshold(
                cv2.GaussianBlur(g, (11, 11), 0), 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
            ),
        ]

        for i, strategy in enumerate(strategies):
            try:
                edged = strategy(gray)
                contours = cv2.findContours(edged.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                grabbed = imutils.grab_contours(contours)
                sorted_contours = sorted(grabbed, key=cv2.contourArea, reverse=True)[:5]

                for contour in sorted_contours:
                    peri = cv2.arcLength(contour, True)
                    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)

                    if len(approx) == 4:
                        # Verify it's a reasonable document size (at least 10% of image area)
                        contour_area = cv2.contourArea(approx)
                        image_area = edged.shape[0] * edged.shape[1]
                        if contour_area > image_area * 0.05:
                            logger.info(f"Document detected with strategy {i + 1}")
                            return approx
            except Exception:
                continue

        return None

    def _order_points(self, pts):
        """Order points: top-left, top-right, bottom-right, bottom-left."""
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect

    def _four_point_transform(self, image, pts):
        """Apply perspective transform to get top-down view."""
        rect = self._order_points(pts)
        (tl, tr, br, bl) = rect

        widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        maxWidth = max(int(widthA), int(widthB))

        heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        maxHeight = max(int(heightA), int(heightB))

        dst = np.array([
            [0, 0],
            [maxWidth - 1, 0],
            [maxWidth - 1, maxHeight - 1],
            [0, maxHeight - 1]], dtype="float32")

        M = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))
        return warped

    def _auto_brightness_contrast(self, gray_image, clip_hist_percent=1):
        """Auto-adjust brightness and contrast."""
        hist = cv2.calcHist([gray_image], [0], None, [256], [0, 256])
        accumulator = np.cumsum(hist)

        maximum = accumulator[-1]
        clip_hist_percent *= (maximum / 100.0) / 2.0

        min_gray = 0
        while accumulator[min_gray] < clip_hist_percent:
            min_gray += 1

        max_gray = 255
        while accumulator[max_gray] >= (maximum - clip_hist_percent):
            max_gray -= 1

        if max_gray <= min_gray:
            return gray_image

        alpha = 255 / (max_gray - min_gray)
        beta = -min_gray * alpha

        adjusted = cv2.convertScaleAbs(gray_image, alpha=alpha, beta=beta)
        return adjusted

    def _enhance_color(self, image):
        """Enhance color image: denoise, sharpen, adjust levels."""
        # Denoise
        denoised = cv2.fastNlMeansDenoisingColored(image, None, 6, 6, 7, 21)

        # Convert to LAB and enhance L channel
        lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

        # Light sharpening
        kernel = np.array([[-0.5, -0.5, -0.5],
                           [-0.5,  5.0, -0.5],
                           [-0.5, -0.5, -0.5]])
        sharpened = cv2.filter2D(enhanced, -1, kernel)

        return sharpened

    def _generate_pdf_b64(self, image):
        """Generate A4 PDF from scan result and return as base64."""
        try:
            tmp_path = f"/tmp/scan_temp_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.jpg"
            cv2.imwrite(tmp_path, image)

            din_a4 = (img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297))
            layout_fun = img2pdf.get_layout_fun(din_a4)

            pdf_bytes = img2pdf.convert(tmp_path, layout_fun=layout_fun)
            os.remove(tmp_path)

            return base64.b64encode(pdf_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"PDF generation error: {e}")
            return None


# Module-level instance for convenience
_scanner = DocumentScanner()

def scan_from_bytes(image_bytes, mode="scan"):
    return _scanner.scan_image_from_bytes(image_bytes, mode)

def scan_from_path(image_path, mode="scan"):
    return _scanner.scan_image_from_path(image_path, mode)
