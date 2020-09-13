#!/usr/bin/env bash

set -xe

# Find the latest version available
GUESTFS_URL="$(
python3 <<EOF
import re
from distutils.version import StrictVersion

import requests

links = re.compile(r"(?<=href=\").*?(?=\")")
version = re.compile(r"(\.?[0-9]+){1,}")
downloads = requests.get("https://download.libguestfs.org/")
versions = [
    v for v in links.findall(downloads.content.decode())
    if v.endswith("stable/")
]
latest_series = sorted(versions, key=lambda v: StrictVersion(v.split('-')[0]), reverse=True)[0]
downloads = requests.get(f"https://download.libguestfs.org/{latest_series}")
versions = [
    v for v in links.findall(downloads.content.decode())
    if v.endswith(".tar.gz")
]
latest_file = sorted(versions, key=lambda v: StrictVersion(version.search(v).group(0)), reverse=True)[0]
print(f"https://download.libguestfs.org/{latest_series}{latest_file}", end="")
EOF
)"

wget "$GUESTFS_URL"
tar xf libguestfs-*.tar.gz
rm *.tar.gz

pushd libguestfs-*
    ./configure CFLAGS=-fPIC --enable-python
    make -j$(nproc)

    pushd python
        sed -i 's/from distutils.core/from setuptools/g' setup.py
        make sdist
        CPPFLAGS="-L../lib/.libs" python3 setup.py bdist_wheel
        auditwheel show dist/*.whl
        cp dist/* /io/
    popd

    # https://build.opensuse.org/package/view_file/openSUSE:Factory/libguestfs/31e6b187-po-Remove-virt-v2v-related-dependency-from-POTFILES-ml..patch?expand=0
    set +e
    cat <<'EOF' | sed 's/ {4}/\t/g' | patch -p1 Makefile.am
--- ../tmp/libguestfs-1.42.0/Makefile.am        2020-03-06 19:31:08.077079274 +0000
+++ Makefile.am 2020-09-13 22:25:33.497466016 +0000
@@ -345,6 +345,7 @@
    cd $(srcdir); \
    find builder common/ml* customize dib get-kernel resize sparsify sysprep -name '*.ml' | \
    grep -v '^builder/templates/' | \
+    grep -v '^common/mlv2v/' | \
    grep -v -E '.*_tests\.ml$$' | \
    LC_ALL=C sort > $@-t
    mv $@-t $@
EOF
    patch -p1 <<'EOF'
diff --git a/po/POTFILES-ml b/po/POTFILES-ml
index a9b6efdaa..2fbdff03d 100644
--- a/po/POTFILES-ml
+++ b/po/POTFILES-ml
@@ -41,7 +41,6 @@ common/mltools/urandom.ml
 common/mltools/xpath_helpers.ml
 common/mlutils/c_utils.ml
 common/mlutils/unix_utils.ml
-common/mlv2v/uefi.ml
 common/mlvisit/visit.ml
 common/mlxml/xml.ml
 customize/append_line.ml
EOF
popd
