#!/bin/bash
# Builds dist/Turtle_System-<version>-x86_64.AppImage: a portable Python 3.12
# (python-appimage, manylinux2014) with PySide6-Essentials and NumPy, plus this program.
# Needs: curl, git, network access. Usage: tools/build_appimage.sh [version]
set -euo pipefail
VERSION=${1:-$(git describe --tags --always 2>/dev/null | sed 's/^v//')}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT
PYAPP=https://github.com/niess/python-appimage/releases/download/python3.12/python3.12.15-cp312-cp312-manylinux2014_x86_64.AppImage
TOOL=https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage

cd "$WORK"
curl -sSL -o python.AppImage "$PYAPP"
curl -sSL -o appimagetool.AppImage "$TOOL"
chmod +x python.AppImage appimagetool.AppImage
./python.AppImage --appimage-extract >/dev/null
mv squashfs-root AppDir
PY=AppDir/opt/python3.12/bin/python3.12
"$PY" -m pip install -q --no-warn-script-location "PySide6-Essentials==6.11.*" numpy

# drop what the program does not use (QML/Quick, Designer, tools, translations, tests, Tk)
SP=AppDir/opt/python3.12/lib/python3.12/site-packages
P6=$SP/PySide6
rm -rf -- "$P6"/{assistant,designer,linguist,lrelease,lupdate,qmlformat,qmllint,qmlls,svgtoqml}
rm -rf -- "$P6"/{doc,include,typesystems,glue,scripts,support}
rm -rf -- "$P6"/Qt/{qml,translations,metatypes,libexec}
rm -f -- "$P6"/QtQml* "$P6"/QtQuick* "$P6"/QtDesigner* "$P6"/QtHelp* "$P6"/QtSql* \
         "$P6"/QtUiTools* "$P6"/QtXml* "$P6"/QtConcurrent* "$P6"/libpyside6qml*
for lib in Quick Qml Designer Help Sql UiTools WaylandCompositor Labs ShaderTools Xml Concurrent; do
    rm -f -- "$P6"/Qt/lib/libQt6${lib}*
done
rm -rf -- "$P6"/Qt/plugins/{designer,qmltooling,qmllint,sqldrivers}
rm -rf -- AppDir/opt/python3.12/lib/python3.12/{test,idlelib,tkinter} AppDir/usr/share/tcltk
rm -rf -- "$SP"/numpy/tests "$SP"/numpy/*/tests
rm -f -- AppDir/*.desktop AppDir/python.png AppDir/.DirIcon

# the program
mkdir -p AppDir/opt/turtle-system
git -C "$ROOT" archive HEAD | tar -x -C AppDir/opt/turtle-system
cat > AppDir/AppRun <<'RUN'
#!/bin/bash
# Turtle System for Linux: run the bundled Python with the application.
if [ -z "${APPDIR:-}" ]; then
    APPDIR="$(dirname "$(readlink -f "$0")")"
fi
export APPDIR
export SSL_CERT_FILE="$APPDIR/opt/_internal/certs.pem"
unset PYTHONHOME PYTHONPATH
exec "$APPDIR/opt/python3.12/bin/python3.12" -s "$APPDIR/opt/turtle-system/turtle_system.py" "$@"
RUN
chmod +x AppDir/AppRun
sed 's|Exec=python3 @APPDIR@/turtle_system.py %f|Exec=turtle-system %f|' \
    "$ROOT/packaging/turtle-system.desktop.in" > AppDir/turtle-system.desktop
cp "$ROOT/icons/turtle-system-256.png" AppDir/turtle-system.png
ln -s turtle-system.png AppDir/.DirIcon

mkdir -p "$ROOT/dist"
ARCH=x86_64 ./appimagetool.AppImage --appimage-extract-and-run AppDir \
    "$ROOT/dist/Turtle_System-${VERSION}-x86_64.AppImage" >/dev/null
echo "Built dist/Turtle_System-${VERSION}-x86_64.AppImage"
