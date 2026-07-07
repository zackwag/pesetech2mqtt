#
# Docker helper script to install BlueZ with bluetooth mesh support
#
set -e

JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"

# clone recent version
wget https://github.com/bluez/bluez/archive/refs/tags/5.66.tar.gz
tar -xvf 5.66.tar.gz
cd bluez-5.66

# configure
./bootstrap
./configure --enable-mesh --enable-testing --enable-tools --prefix=/usr --mandir=/usr/share/man --sysconfdir=/etc --localstatedir=/var

# build and install
make -j"$JOBS"
make install
