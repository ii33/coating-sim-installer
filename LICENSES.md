# Distribution notices

Installer source and the original public Coating Sim icon retain the root MIT license and original copyright notices.

The Windows onefile executable contains CPython 3.13.16, its Windows standard-library components, Tcl/Tk (the tested runtime reports 8.6.15), OpenSSL 3.5.9, and PyInstaller 6.22.3's loader/runtime. Their upstream licenses continue to apply; the installer MIT license does not replace them.

- `tools/dependency_manager/licenses/CPython-bundled-Windows-LICENSE.txt` is copied unchanged from the actual Windows CPython installation used for the build.
- `Tk-bundled-license.terms` is copied unchanged from that installation's Tk library.
- `PyInstaller-6.22.3-COPYING.txt` includes the PyInstaller license and bootloader exception.
- The remaining public upstream texts and provenance records were retained with the inherited dependency tool. A retained historical inventory is not a claim that every listed historical Linux component is bundled in this Windows executable.

The same notices are embedded in the onefile archive. SHA256SUMS.txt identifies the release executable; build dependencies are pinned separately from the project environments managed by the installer.
