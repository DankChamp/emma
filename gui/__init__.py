"""
Emma Desktop - the PySide6 control room for the Emma backend.

This package is intentionally separate from the FastAPI app: the GUI is
just another client of Emma's HTTP API, the same way a future phone app
or ESP32 voice device would be. It never imports from `core/` directly -
everything goes through `gui.api_client`.
"""
