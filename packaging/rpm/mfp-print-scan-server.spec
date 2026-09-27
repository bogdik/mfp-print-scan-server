%if 0%{!?_unitdir:1}
%global _unitdir %{_prefix}/lib/systemd/system
%endif

Name:           mfp-print-scan-server
Version:        %{_mfp_version}
Release:        1%{?dist}
Summary:        Turn a USB printer/scanner into a network device
License:        MIT
URL:            https://github.com/bogdik/mfp-print-scan-server
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.10
Requires:       python3-pip
Requires:       cups
Requires:       cups-client
Requires:       systemd
Recommends:     sane-backends

%description
A self-hosted web server that gives a USB printer and/or scanner a
web UI, an IPP network-printer endpoint, and AirScan/eSCL network
scanning, so any device on the LAN can print and scan without
installing drivers.

Installs to /opt/mfp-print-scan-server (own Python virtual
environment, dependencies fetched from PyPI on first install — this
needs internet access once) and runs as a systemd service under a
dedicated "mfp" user. Configuration lives in
/opt/mfp-print-scan-server/config.ini.

%prep
%setup -q

%build
# Nothing to build — pure Python, dependencies are installed into a venv
# on the target machine at %post time (see below).

%install
rm -rf %{buildroot}
install -d %{buildroot}/opt/mfp-print-scan-server
cp -r app %{buildroot}/opt/mfp-print-scan-server/app
cp run.py requirements.txt config.example.ini start.sh README.md README.ru.md LICENSE \
    %{buildroot}/opt/mfp-print-scan-server/
install -d %{buildroot}/opt/mfp-print-scan-server/uploads
find %{buildroot}/opt/mfp-print-scan-server -name '__pycache__' -type d -exec rm -rf {} + || true

install -d %{buildroot}%{_unitdir}
install -m 0644 packaging/rpm/mfp-print-scan-server.service %{buildroot}%{_unitdir}/

%pre
getent group mfp >/dev/null || groupadd -r mfp
getent passwd mfp >/dev/null || useradd -r -g mfp -d /opt/mfp-print-scan-server -s /sbin/nologin \
    -c "MFP Print & Scan Server" mfp
exit 0

%post
# Access to the printer/scanner device — skip a group this system doesn't have.
for grp in lp scanner; do
    getent group "$grp" >/dev/null && usermod -aG "$grp" mfp || true
done
mkdir -p /opt/mfp-print-scan-server/data /opt/mfp-print-scan-server/scans \
    /opt/mfp-print-scan-server/uploads /opt/mfp-print-scan-server/logs
chown -R mfp:mfp /opt/mfp-print-scan-server

echo "Setting up the Python environment (needs internet access, first install only) ..."
su -s /bin/sh mfp -c "/opt/mfp-print-scan-server/start.sh --setup-only"

systemctl daemon-reload || true
systemctl enable --now mfp-print-scan-server.service || true

echo ""
echo "MFP Print & Scan Server: http://localhost:8000"
echo "Configuration: /opt/mfp-print-scan-server/config.ini (created from config.example.ini on first run)"
echo "Logs: journalctl -u mfp-print-scan-server -f"

%preun
# $1 == 0: final removal. $1 >= 1: an upgrade — leave the running service alone.
if [ "$1" -eq 0 ]; then
    systemctl --no-reload disable --now mfp-print-scan-server.service || true
fi

%postun
systemctl daemon-reload || true
if [ "$1" -ge 1 ]; then
    # Upgrade: restart with the new files.
    systemctl try-restart mfp-print-scan-server.service || true
fi
if [ "$1" -eq 0 ]; then
    # Final removal (not an upgrade). The venv is just installed dependencies
    # — safe to drop. config.ini, data/, scans/ and uploads/ are the user's
    # own settings and print/scan history: never deleted automatically.
    rm -rf /opt/mfp-print-scan-server/.venv /opt/mfp-print-scan-server/logs
    getent passwd mfp >/dev/null && userdel mfp >/dev/null 2>&1 || true
    getent group mfp >/dev/null && groupdel mfp >/dev/null 2>&1 || true
    if [ -d /opt/mfp-print-scan-server ] && [ -n "$(ls -A /opt/mfp-print-scan-server 2>/dev/null)" ]; then
        echo "mfp-print-scan-server removed. Your config and history are still in /opt/mfp-print-scan-server"
        echo "(config.ini, data/, scans/, uploads/) — delete that folder by hand if you don't need them."
    fi
fi

%files
%dir /opt/mfp-print-scan-server
/opt/mfp-print-scan-server/app
/opt/mfp-print-scan-server/run.py
/opt/mfp-print-scan-server/requirements.txt
/opt/mfp-print-scan-server/config.example.ini
/opt/mfp-print-scan-server/start.sh
/opt/mfp-print-scan-server/README.md
/opt/mfp-print-scan-server/README.ru.md
/opt/mfp-print-scan-server/LICENSE
%dir /opt/mfp-print-scan-server/uploads
%{_unitdir}/mfp-print-scan-server.service

%changelog
* Sun Sep 27 2026 bogdik <noreply@example.com> - 0.1.0-1
- Initial package.
