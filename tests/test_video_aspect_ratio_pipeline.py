import os
import uuid
import tempfile
import cv2
import numpy as np
import pytest

from app.services.video_processing_service import VideoProcessingService


def test_standard_aspect_ratios():
    """Requirement 1: Verify correct resolutions for 9:16, 4:5, 1:1, and 16:9."""
    w_916, h_916, r_916 = VideoProcessingService.resolve_target_dimensions("9:16")
    assert (w_916, h_916) == (1080, 1920)
    assert r_916 == "9:16"

    w_45, h_45, r_45 = VideoProcessingService.resolve_target_dimensions("4:5")
    assert (w_45, h_45) == (1080, 1350)
    assert r_45 == "4:5"

    w_11, h_11, r_11 = VideoProcessingService.resolve_target_dimensions("1:1")
    assert (w_11, h_11) == (1080, 1080)
    assert r_11 == "1:1"

    w_169, h_169, r_169 = VideoProcessingService.resolve_target_dimensions("16:9")
    assert (w_169, h_169) == (1920, 1080)
    assert r_169 == "16:9"


def test_platform_target_format_determination():
    """Requirement 1 & 5: Determine target format from destination platform and post type."""
    # Instagram Reels -> 9:16 (1080x1920)
    w, h, r = VideoProcessingService.get_platform_target_format("instagram", "reel")
    assert (w, h, r) == (1080, 1920, "9:16")

    # Facebook Reels -> 9:16 (1080x1920)
    w_fb, h_fb, r_fb = VideoProcessingService.get_platform_target_format("facebook", "reel")
    assert (w_fb, h_fb, r_fb) == (1080, 1920, "9:16")

    # YouTube Shorts -> 9:16 (1080x1920)
    w_ys, h_ys, r_ys = VideoProcessingService.get_platform_target_format("youtube", "short")
    assert (w_ys, h_ys, r_ys) == (1080, 1920, "9:16")

    # YouTube Standard Video -> 16:9 (1920x1080)
    w_yt, h_yt, r_yt = VideoProcessingService.get_platform_target_format("youtube", "post")
    assert (w_yt, h_yt, r_yt) == (1920, 1080, "16:9")

    # Instagram Feed Portrait -> 4:5 (1080x1350)
    w_ip, h_ip, r_ip = VideoProcessingService.get_platform_target_format("instagram", "portrait")
    assert (w_ip, h_ip, r_ip) == (1080, 1350, "4:5")

    # TikTok -> 9:16 (1080x1920)
    w_tt, h_tt, r_tt = VideoProcessingService.get_platform_target_format("tiktok", "video")
    assert (w_tt, h_tt, r_tt) == (1080, 1920, "9:16")


def test_no_stretch_intelligent_reframe():
    """Requirement 2 & 3: Never compress or stretch; preserve original proportions and subject."""
    # Create a 16:9 landscape test frame with a white square in center
    src_w, src_h = 1920, 1080
    frame = np.zeros((src_h, src_w, 3), dtype=np.uint8)

    # Draw a 200x200 square at the exact center
    sq_size = 200
    cx, cy = src_w // 2, src_h // 2
    frame[cy - sq_size // 2:cy + sq_size // 2, cx - sq_size // 2:cx + sq_size // 2] = [255, 255, 255]

    # Reframe to 9:16 portrait (1080x1920)
    reframed = VideoProcessingService.reframe_frame_without_distortion(frame, 1080, 1920)

    assert reframed.shape == (1920, 1080, 3)

    # Find white square in reframed image
    white_pixels = np.where(reframed[:, :, 0] == 255)
    assert len(white_pixels[0]) > 0, "Central subject must remain visible after reframing"

    y_min, y_max = np.min(white_pixels[0]), np.max(white_pixels[0])
    x_min, x_max = np.min(white_pixels[1]), np.max(white_pixels[1])

    ref_w = x_max - x_min
    ref_h = y_max - y_min

    # In reframed 9:16, the square's aspect ratio (ref_w / ref_h) MUST remain 1:1 (~1.0), NOT stretched!
    aspect_of_square = ref_w / float(ref_h)
    assert abs(aspect_of_square - 1.0) < 0.05, f"Subject was stretched or distorted! Ratio was {aspect_of_square}"


def test_ffmpeg_video_standardization_and_validation():
    """Requirement 4 & 6: FFmpeg standardizes video to 9:16 with H.264/AAC without distortion."""
    temp_dir = tempfile.gettempdir()
    input_video = os.path.join(temp_dir, f"test_raw_{uuid.uuid4().hex[:8]}.mp4")
    output_video = os.path.join(temp_dir, f"test_std_{uuid.uuid4().hex[:8]}.mp4")

    try:
        # Create a 16:9 landscape test video
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        fps = 30
        vw = cv2.VideoWriter(input_video, fourcc, fps, (1280, 720))
        for i in range(15):  # 0.5s
            test_frame = np.full((720, 1280, 3), (i * 15, 100, 200), dtype=np.uint8)
            cv2.circle(test_frame, (640, 360), 80, (255, 255, 255), -1)
            vw.write(test_frame)
        vw.release()

        assert os.path.exists(input_video) and os.path.getsize(input_video) > 500

        # Standardize to 9:16 Reel format
        result = VideoProcessingService.process_and_standardize_video(
            input_path=input_video,
            output_path=output_video,
            target_aspect_ratio="9:16"
        )

        assert result["is_valid"] is True
        assert result["width"] == 1080
        assert result["height"] == 1920
        assert result["aspect_ratio"] == "9:16"
        assert os.path.exists(output_video)
        assert os.path.getsize(output_video) > 1000

        # Probe the output file
        probe = VideoProcessingService.probe_media(output_video)
        assert probe["width"] == 1080
        assert probe["height"] == 1920
        assert probe["has_audio"] is True

    finally:
        for f in (input_video, output_video):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

