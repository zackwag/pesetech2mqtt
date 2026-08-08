set -e

JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
wget https://github.com/bluez/bluez/archive/refs/tags/5.66.tar.gz
tar -xvf 5.66.tar.gz
cd bluez-5.66
./bootstrap
./configure --enable-mesh --enable-testing --enable-tools --prefix=/usr \
  --mandir=/usr/share/man --sysconfdir=/etc --localstatedir=/var
make -j"$JOBS"
make install
