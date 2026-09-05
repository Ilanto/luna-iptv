#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$project_dir"

for tool in python3 rpmbuild desktop-file-validate tar; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Required build tool is missing: $tool" >&2
        echo "On openSUSE, install rpm-build, python3 and desktop-file-utils." >&2
        exit 1
    fi
done

for required in README.md LICENSE pyproject.toml luna_iptv/app.py packaging/luna-iptv.spec; do
    if [[ ! -f "$required" ]]; then
        echo "Cannot package an incomplete source tree: $required is missing." >&2
        exit 1
    fi
done

package_version=$(python3 - <<'PY'
from pathlib import Path
import re
import tomllib

version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", version):
    raise SystemExit(f"Unsupported RPM version: {version}")
match = re.search(r"^Version:\s*(\S+)", Path("packaging/luna-iptv.spec").read_text(), re.M)
if match is None or match[1] != version:
    raise SystemExit("Version differs between pyproject.toml and packaging/luna-iptv.spec")
print(version)
PY
)

package_name="luna-iptv-$package_version"
rpm_build_dir="$project_dir/build/rpm"
distribution_dir="$project_dir/dist"
mkdir -p -- "$distribution_dir" "$rpm_build_dir/BUILD" "$rpm_build_dir/BUILDROOT" \
    "$rpm_build_dir/RPMS" "$rpm_build_dir/SOURCES" "$rpm_build_dir/SPECS" "$rpm_build_dir/SRPMS"

# Explicit sources keep playlists, credentials, local dependencies and the venv
# out of both the source archive and the source RPM.
source_paths=(luna_iptv tests scripts packaging assets pyproject.toml README.md LICENSE)
if [[ -d docs ]]; then
    source_paths+=(docs)
fi
tar --sort=name --exclude='__pycache__' --exclude='*.pyc' \
    --transform="s,^,$package_name/," \
    -czf "$rpm_build_dir/SOURCES/$package_name.tar.gz" -- "${source_paths[@]}"
cp -- packaging/luna-iptv.spec "$rpm_build_dir/SPECS/luna-iptv.spec"

rpmbuild --define "_topdir $rpm_build_dir" -ba "$rpm_build_dir/SPECS/luna-iptv.spec"

cp -- "$rpm_build_dir/SOURCES/$package_name.tar.gz" "$distribution_dir/"
find "$rpm_build_dir/RPMS" "$rpm_build_dir/SRPMS" -type f \
    -name "$package_name-*.rpm" -exec cp -- '{}' "$distribution_dir/" \;
echo "RPM, source RPM and source archive are available in $distribution_dir"
