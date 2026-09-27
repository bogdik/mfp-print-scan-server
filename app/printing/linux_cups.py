import http.client
import re
import subprocess
import time
from pathlib import Path

from ..i18n import t
from ..ipp import protocol as ipp
from .base import OptionChoice, PrintBackend, PrinterInfo, PrinterOption, PrintError, PrinterStatus, SupplyLevel
from .convert import to_pdf
from .maintenance import TEST_PAGE, MaintenanceAction, actions_for, raw_command

CUPS_TEST_PAGE = Path("/usr/share/cups/data/testprint")

# IPP printer-state (RFC 8011 section 5.4.12).
IPP_STATE = {3: "idle", 4: "printing", 5: "stopped"}


def _cups_ipp_query(printer_name: str, requested: list[str]) -> dict[str, list]:
    """Queries CUPS's own IPP server (always on localhost:631, CUPS being a
    requirement already) for the given printer attributes — reusing our own
    IPP codec (app/ipp/protocol.py; the wire format is symmetric, so the
    same encode/decode functions work for either direction) instead of a
    new dependency (pycups) or parsing `lpstat`'s locale-dependent text."""
    request_id = int(time.time()) & 0x7FFFFFFF
    groups = [(ipp.TAG_OPERATION, [
        ipp.Attr(ipp.TAG_CHARSET, "attributes-charset", ["utf-8"]),
        ipp.Attr(ipp.TAG_LANGUAGE, "attributes-natural-language", ["en"]),
        ipp.Attr(ipp.TAG_URI, "printer-uri", [f"ipp://localhost/printers/{printer_name}"]),
        ipp.Attr(ipp.TAG_KEYWORD, "requested-attributes", requested),
    ])]
    body = ipp.encode_response((2, 0), ipp.OP_GET_PRINTER_ATTRIBUTES, request_id, groups)
    conn = http.client.HTTPConnection("localhost", 631, timeout=5)
    try:
        conn.request("POST", f"/printers/{printer_name}", body=body, headers={"Content-Type": "application/ipp"})
        data = conn.getresponse().read()
    finally:
        conn.close()
    parsed = ipp.decode_request(data)
    return {name: attr.values for name, attr in parsed.group(ipp.TAG_PRINTER).items()}

# PPD options that duplicate another option or aren't meaningful per-job —
# hidden from the UI. Everything else the driver reports (PageSize,
# MediaType, cupsPrintQuality/StpQuality, ColorModel, Duplex, ...) is shown
# as-is, so any printer's real capabilities show up without hardcoding
# per-manufacturer option names.
HIDDEN_OPTION_KEYS = {"PageRegion"}

# Fine-tuning sliders (color balance, gamma, contrast, ...) show up in PPDs
# as long numeric ranges like "-50 -49 ... 0 ... 50" — not what "paper type /
# quality / size" means in practice, so options with too many choices are
# dropped rather than rendered as a 100-item dropdown.
MAX_CHOICES = 15

_OPTION_LINE_RE = re.compile(r"^(?P<key>\S+?)/(?P<label>[^:]*):\s*(?P<choices>.+)$")


class CupsPrintBackend(PrintBackend):
    def list_printers(self) -> list[PrinterInfo]:
        try:
            result = subprocess.run(["lpstat", "-a"], capture_output=True, text=True, timeout=5)
        except FileNotFoundError as exc:
            raise PrintError(t("err.cups_missing", cmd="lpstat")) from exc

        default = self._default_printer()
        printers = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            name = line.split()[0]
            printers.append(PrinterInfo(name=name, is_default=(name == default)))
        return printers

    def _default_printer(self) -> str | None:
        try:
            result = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, timeout=5)
        except FileNotFoundError:
            return None
        line = result.stdout.strip()
        if ":" in line:
            return line.split(":", 1)[1].strip()
        return None

    def list_options(self, printer_name: str) -> list[PrinterOption]:
        try:
            result = subprocess.run(
                ["lpoptions", "-p", printer_name, "-l"], capture_output=True, text=True, timeout=5
            )
        except FileNotFoundError as exc:
            raise PrintError(t("err.cups_missing", cmd="lpoptions")) from exc

        if result.returncode != 0:
            raise PrintError(result.stderr.strip() or t("err.cmd_failed", cmd="lpoptions"))

        options = []
        for line in result.stdout.splitlines():
            match = _OPTION_LINE_RE.match(line.strip())
            if not match:
                continue
            key = match.group("key")
            if key in HIDDEN_OPTION_KEYS:
                continue

            choices = []
            default = None
            for token in match.group("choices").split():
                is_default = token.startswith("*")
                value = token[1:] if is_default else token
                if "custom" in value.lower():
                    # e.g. PPD's "Custom.WIDTHxHEIGHT" placeholder — needs
                    # extra dimension parameters our simple key=value form
                    # can't supply, so it isn't offered as a plain choice.
                    continue
                if is_default:
                    default = value
                choices.append(OptionChoice(value=value, label=value))

            if len(choices) < 2 or len(choices) > MAX_CHOICES:
                continue
            if default is None:
                default = choices[0].value

            options.append(
                PrinterOption(key=key, label=match.group("label").strip() or key, choices=choices, default=default)
            )
        return options

    def print_file(
        self,
        file_path: Path,
        printer_name: str | None,
        copies: int = 1,
        options: dict[str, str] | None = None,
        scaling: str = "fit",  # CUPS applies its own scaling; IPP isn't served on Linux
    ) -> None:
        # Office documents / text go through LibreOffice → PDF when it's
        # installed, so the printout matches the preview; otherwise CUPS gets
        # the original file.
        path_to_print = to_pdf(file_path) or file_path
        cmd = ["lp"]
        if printer_name:
            cmd += ["-d", printer_name]
        cmd += ["-n", str(copies)]
        for key, value in (options or {}).items():
            cmd += ["-o", f"{key}={value}"]
        cmd += [str(path_to_print)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError as exc:
            raise PrintError(t("err.cups_missing", cmd="lp")) from exc

        if result.returncode != 0:
            raise PrintError(result.stderr.strip() or t("err.cmd_failed", cmd="lp"))

    def _make_and_model(self, printer_name: str) -> str:
        try:
            out = subprocess.run(["lpoptions", "-p", printer_name], capture_output=True, text=True, timeout=5).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            out = ""
        match = re.search(r"printer-make-and-model='([^']*)'", out)
        return match.group(1) if match else printer_name

    def maintenance_actions(self, printer_name: str) -> list[MaintenanceAction]:
        return actions_for(self._make_and_model(printer_name))

    def run_maintenance(self, printer_name: str, action_id: str) -> None:
        make_and_model = self._make_and_model(printer_name)
        if action_id not in {a.id for a in actions_for(make_and_model)}:
            raise PrintError(t("err.action_unsupported"))
        if action_id == TEST_PAGE.id:
            if not CUPS_TEST_PAGE.exists():
                raise PrintError(t("err.cups_test_page_missing", path=CUPS_TEST_PAGE))
            cmd, data = ["lp", "-d", printer_name, "-t", TEST_PAGE.label, str(CUPS_TEST_PAGE)], None
        else:
            cmd = ["lp", "-d", printer_name, "-o", "raw", "-t", f"MFP: {action_id}", "-"]
            data = raw_command(make_and_model, action_id)
        try:
            result = subprocess.run(cmd, input=data, capture_output=True, timeout=30)
        except FileNotFoundError as exc:
            raise PrintError(t("err.cups_missing", cmd="lp")) from exc
        if result.returncode != 0:
            raise PrintError(result.stderr.decode(errors="replace").strip() or t("err.cmd_failed", cmd="lp"))

    def printer_status(self, printer_name: str) -> PrinterStatus | None:
        try:
            attrs = _cups_ipp_query(
                printer_name, ["printer-state", "printer-state-reasons", "printer-is-accepting-jobs"]
            )
        except (OSError, ipp.IppError):
            return None
        state = IPP_STATE.get((attrs.get("printer-state") or [None])[0], "unknown")
        reasons = [r for r in (attrs.get("printer-state-reasons") or []) if r != "none"]
        accepting = (attrs.get("printer-is-accepting-jobs") or [True])[0]
        return PrinterStatus(state=state, reasons=reasons, accepting_jobs=bool(accepting))

    def supply_levels(self, printer_name: str) -> list[SupplyLevel] | None:
        """Many CUPS drivers (HPLIP, some manufacturer PPDs) report ink/toner
        levels as IPP marker-* attributes; the Gutenprint/generic-USB setup
        this project's own Canon MG2500 uses doesn't, so this returns None
        for it — but it works out of the box for printers whose driver does
        populate these, no code changes needed."""
        try:
            attrs = _cups_ipp_query(printer_name, ["marker-names", "marker-levels", "marker-types"])
        except (OSError, ipp.IppError):
            return None
        names, levels, types = (attrs.get(k) or [] for k in ("marker-names", "marker-levels", "marker-types"))
        if not names or not levels:
            return None
        supplies = []
        for i, name in enumerate(names):
            level = levels[i] if i < len(levels) else None
            kind = "toner" if i < len(types) and "toner" in types[i] else "ink"
            supplies.append(SupplyLevel(name=name, percent=level if level is not None and level >= 0 else None, kind=kind))
        return supplies
