set -e

JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
git clone https://git.kernel.org/pub/scm/libs/ell/ell.git
cd ell
git checkout 0.54
./bootstrap
./configure --prefix=/usr
make -j"$JOBS"
make install
