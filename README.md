# OpenWrt router images

Thin adapter around the pinned upstream OpenWrt 25.12.6 ImageBuilder and SDK.
Upstream owns the Linux kernel, drivers, device trees, package selection, image
formats and flash size checks. This repository does not emulate board support.

The platform writes `config/openwrt.json` with exact `target`, `profile`,
`architecture`, `arch_packages`, package additions/removals and pinned ImageBuilder
and SDK source descriptors from its generated release registry. Release checksums
are independently verified against the retained OpenWrt public signing key.

Author files under `config/files` for the native ImageBuilder overlay. Custom
OpenWrt package directories (with their own native Makefiles) go under
`config/sdk-packages`; the matching SDK compiles these before image assembly.
These directories are private build inputs and are not committed by this template.

Run `./build.sh`. The platform's privileged builder uses the idempotent
`install-dependencies.sh` to provision missing standard prerequisites on Fedora
or Debian. An ordinary user receives a precise prerequisite error and can run
that installer separately as root. Dependencies: Python 3.12+, make,
gcc, g++, binutils, gawk, git, rsync, unzip, zstd, GnuPG/gpgv and the normal
OpenWrt ImageBuilder prerequisites. `OPENWRT_CACHE_DIR` may point to a private
archive cache. Downloads are hash checked and archive extraction uses the
standard library data filter. No installation, flashing, host network changes,
or hardware probes are performed.

`output/openwrt-firmware.zip` preserves native image names and installation types,
checksums, packages/build metadata, source provenance and installation limitations.
Bootloader-write artifacts are deliberately excluded. The build result proves
assembly and integrity only: actual router boot, Wi-Fi, Ethernet and recovery
must be tested separately on the exact hardware revision.

The adapter is MIT-licensed. Included upstream software retains its own
licences; package lists and exact upstream source revision are retained with the
bundle. Corresponding upstream sources are available from the OpenWrt source
and release repositories referenced in the manifest.
