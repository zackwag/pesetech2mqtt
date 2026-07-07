#
# Docker helper script to get Embedded Linux library
#
set -e

JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"

# clone repository
git clone https://git.kernel.org/pub/scm/libs/ell/ell.git
cd ell

# checkout recent version
git checkout 0.54

# configure, build, and install
./bootstrap
./configure --prefix=/usr
make -j"$JOBS"
make install
