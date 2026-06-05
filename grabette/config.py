"""Configuration management using Pydantic Settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "GRABETTE_"}

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Backend
    backend: str = "auto"  # "auto", "mock", or "rpi"

    # Data
    data_dir: Path = Path.home() / "grabette-data"

    # Camera
    camera_fps: int = 46
    camera_resolution_w: int = 1296
    camera_resolution_h: int = 972

    # IMU
    imu_hz: int = 200

    # Angle sensors (AS5600 on I2C buses 4 & 5)
    angle_sensors: bool = True

    # OAK-D SR — default OFF to save battery. Toggle from the UI to enable.
    enable_oakd: bool = False
    # After a capture that auto-enabled the OAK-D, keep it warm this many
    # seconds before powering down — lets back-to-back recordings start
    # instantly instead of paying the cold-boot warmup each time.
    oakd_keepalive_s: float = 30.0

    # UI
    ui_enabled: bool = True

    # Hardware button (Grove LED Button on GPIO22/23)
    button_enabled: bool = True

    # Logging
    log_level: str = "INFO"

    # Robot ID — identifies this grabette unit when multiple units are in use (1, 2, 3, ...)
    # Set via GRABETTE_ROBOT_ID=2 in /etc/grabette.env
    robot_id: int = 1

    # Robot type — "l" for lgrabette, "r" for rgrabette
    # Set via GRABETTE_ROBOT_TYPE=l (or r) in /etc/grabette.env
    robot_type: str = "l"

    # Hotspot password — set via GRABETTE_HOTSPOT_PASSWORD in /etc/grabette.env
    # Must match GRABETTE_HOTSPOT_PASS in grabette-screen/src/config.h
    hotspot_password: str = "grabette"

    @property
    def hotspot_ssid(self) -> str:
        """Hotspot SSID derived from the system hostname when it matches [lr]grabette(-\\d+)?.
        Falls back to GRABETTE_ROBOT_TYPE + GRABETTE_ROBOT_ID if the hostname doesn't match."""
        import re
        import socket
        hostname = socket.gethostname()
        if re.match(r"^[lr]grabette(-\d+)?$", hostname):
            return hostname
        return f"{self.robot_type}grabette-{self.robot_id}"

    # File written by the BLE service (root) and read by the API (rasp user)
    hotspot_credentials_file: Path = Path("/var/lib/grabette/wifi_credentials.json")


settings = Settings()
