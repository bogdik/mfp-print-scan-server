import re
import shutil
import subprocess
from pathlib import Path

from ..i18n import t
from .base import OptionChoice, PrintBackend, PrinterInfo, PrinterOption, PrintError
from .maintenance import TEST_PAGE, MaintenanceAction, actions_for, raw_command

CUPS_TEST_PAGE = Path("/usr/share/cups/data/testprint")

# Office formats CUPS can't render directly — converted to PDF via LibreOffice
# first, if it's installed.
CONVERTIBLE_EXTENSIONS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".rtf"}

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
        path_to_print = self._maybe_convert(file_path)
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

    def _maybe_convert(self, file_path: Path) -> Path:
        if file_path.suffix.lower() not in CONVERTIBLE_EXTENSIONS:
            return file_path

        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            return file_path

        outdir = file_path.parent
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(file_path)],
            capture_output=True,
            timeout=60,
        )
        converted = outdir / (file_path.stem + ".pdf")
        return converted if converted.exists() else file_path
