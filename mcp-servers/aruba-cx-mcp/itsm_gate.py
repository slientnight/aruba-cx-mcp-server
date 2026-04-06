"""ITSM gate for validating Change Request numbers.

Validates change_request_number format before allowing write operations.
Disabled by default. Enable with ITSM_ENABLED=true.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# Regex pattern for valid Change Request numbers: "CHG" followed by one or more digits
_CHG_PATTERN = re.compile(r"^CHG\d+$")


def validate_change_request(change_request_number: str) -> None:
    """Validate a Change Request number.

    When ITSM_ENABLED is false or unset, this is a no-op.
    When enabled, validates format (CHG followed by digits).

    Args:
        change_request_number: The Change Request number to validate.

    Raises:
        ValueError: If ITSM is enabled and the change_request_number is
            empty, missing, or has an invalid format.
    """
    itsm_enabled = os.environ.get("ITSM_ENABLED", "false").lower() == "true"

    if not itsm_enabled:
        return

    # ITSM is enabled — change_request_number is required
    if not change_request_number:
        raise ValueError(
            "ITSM gate is enabled but no change_request_number was provided. "
            "A valid Change Request number (e.g., CHG0012345) is required for write operations."
        )

    # Validate format: must match CHG followed by one or more digits
    if not _CHG_PATTERN.match(change_request_number):
        raise ValueError(
            f"Invalid change_request_number format: '{change_request_number}'. "
            "Expected format: CHG followed by one or more digits (e.g., CHG0012345)."
        )

    # Lab mode — skip external API call
    lab_mode = os.environ.get("ITSM_LAB_MODE", "false").lower() == "true"

    if lab_mode:
        logger.info(
            "Lab mode active — skipping API check for %s",
            change_request_number,
        )
        return

    logger.info("CR %s format validated", change_request_number)
