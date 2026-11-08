#!/bin/sh
# Canonical host prerequisites for this native ImageBuilder/SDK adapter.
set -eu
check() {
    for tool in python3 make gcc g++ git gawk rsync unzip zstd gpg gpgv wget file patch; do
        command -v "$tool" >/dev/null 2>&1 || return 1
    done
    perl -MFindBin -MJSON::PP -MFile::Copy -MData::Dumper -e 1 >/dev/null 2>&1
}
if check; then exit 0; fi
if [ "${1:-}" = --check ]; then
    echo 'OpenWrt requires native build tools and complete Perl core modules (FindBin, JSON::PP, File::Copy, Data::Dumper). Run install-dependencies.sh as root.' >&2
    exit 1
fi
if [ "$(id -u)" != 0 ]; then
    echo 'OpenWrt build prerequisites are missing. Run install-dependencies.sh as root, then retry.' >&2
    exit 1
fi
if command -v dnf >/dev/null 2>&1; then
    dnf install -y gcc gcc-c++ binutils bzip2 gawk gettext git ncurses-devel make patch perl-core python3 python3-setuptools rsync tar unzip wget which zlib-devel file diffutils findutils zstd gnupg2 openssl-devel
elif command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential gawk gettext git libncurses-dev libssl-dev libelf-dev make patch perl python3 python3-setuptools rsync tar unzip wget zlib1g-dev file diffutils findutils zstd gnupg gpgv ca-certificates
else
    echo 'Install the OpenWrt ImageBuilder/SDK prerequisites for this host distribution.' >&2
    exit 1
fi
check
