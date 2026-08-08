set -e

JOBS="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
wget https://github.com/json-c/json-c/archive/refs/tags/json-c-0.16-20220414.tar.gz
tar -xvf json-c-0.16-20220414.tar.gz
cd json-c-json-c-0.16-20220414
mkdir build
cd build
cmake -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_STATIC_LIBS=OFF -DBUILD_TESTING=OFF ..
make -j"$JOBS"
make install
