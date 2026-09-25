# Scanning, structured like app/printing:
#   - base.py: ScanBackend(ABC) — list_scanners() / capabilities() / scan()
#   - windows_wia.py: WIA (Windows Image Acquisition) via COM
#   - linux_sane.py: SANE via the scanimage utility
#   - factory.py: picks the backend by platform.system()
#   - store.py: finished scans on disk (thumbnails, merging into PDF)
