"""Pytest fixtures for the ista Online tests."""
import sys

import pytest
import pytest_socket

# On Windows asyncio's event loop needs an internal AF_INET socket pair, which
# pytest-homeassistant-custom-component's socket blocking rejects (it only allows
# unix sockets, fine on Linux/macOS). All real network access in these tests is
# mocked, so neuter the blocking on Windows before the plugin can enable it.
if sys.platform == "win32":
    pytest_socket.disable_socket = lambda *args, **kwargs: None

pytest_plugins = "pytest_homeassistant_custom_component"

# Note: enable_custom_integrations depends on `hass`, so it must not be autouse —
# that would set up `hass` before `recorder_mock` and break recorder tests. Each
# test that needs the integration loaded requests it explicitly (recorder_mock
# first where applicable).
