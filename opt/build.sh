#!/bin/bash

set -o errexit -o pipefail
export FORCE_UNSAFE_CONFIGURE=1 # Allows buildroot/tar to run as root user in docker container

# global variables
cur_dir_name=${PWD##*/}
cur_dir=$(pwd)
# Doomsigner's own app fork, so `./opt/build.sh --pi0` with no flags builds THIS
# device rather than somebody else's. Its `dev` tracks 3rdIteration's dev, which is
# what this image has always been built from, plus the BIP-352 silent payments work.
#
# It has to track dev, not a release tag: download_app_repo() below runs
# tools/write_versionfile.py out of the app repo, and that script exists only on
# dev. A branch cut from tag SeSi-0.8.7+ShSi-B11 failed CI for exactly that reason.
#
# Override with --app-repo / --app-branch to build stock instead.
seedsigner_app_repo="https://github.com/bitsagarob/doomsigner.git"
seedsigner_app_repo_branch="dev"

help()
{
  echo "Usage: build.sh [pi board] [options]
  
  Pi Board: (only one allowed)
  -a, --all         Build for all supported pi boards
      --pi0         Build for pi0 and pi0w
      --pi2         Build for pi2
      --pi02w       Build for pi02w and pi3
      --pi4         Build for pi4 and pi4cmio
      --lafrite     Build for La Frite AML-S805X-AC

  Options:
  -h, --help           Display a help screen and quit
      --dev            Builds developer version of images
      --smartcard      Builds with smartcard support
      --no-clean       Leave previous build, target, and output files
      --skip-repo      Skip pulling repo, assume rootfs-overlay/opt is populated with app code
      --app-repo       Build image with not official seedsigner github repo
      --app-branch     Build image with repo branch other than default
      --app-commit-id  Build image with specific repo commit id
      --no-op          All other option ignored and script just hangs to keep container alive"
  exit 2
}

tail_endless() {
  echo "Running 'tail -f /dev/null' to keep script alive"
  tail -f /dev/null
  exit 0
}

compile_translations_and_fonts() {

  # create virtual env to compile translation files and slim down font files
  virtualenv .translation-venv
  source .translation-venv/bin/activate
  cd ${rootfs_overlay}/opt

  ss_translations_repo="./src/seedsigner/resources/seedsigner-translations"

  # install depedencies for babel and fonttools(pyftsubset)
  pip install babel || exit
  pip install fonttools || exit
  pip install -e . || exit

  # remove any existing binary mo files if they exist
  rm -rf ${ss_translations_repo}/l10n/**/**/*.mo

  # generate messages.mo files for each translation
  python3 setup.py compile_catalog || exit

  # extract characters from all messages.mo translations into all_chars shell variable
  all_chars=""
  for f in ${ss_translations_repo}/l10n/*/LC_MESSAGES/messages.mo; do
    # extract just the locale name from the path (e.g. "ca" from ".../l10n/ca/LC_MESSAGES/messages.mo")
    locale=$(basename "$(dirname "$(dirname "$f")")")
    output_chars=$(cd ${ss_translations_repo}/tools && python3 extract_characters_from_babel_mo.py "$locale") || echo "Warning: failed to extract chars for locale: $locale" >&2
    all_chars="${all_chars}${output_chars}"
  done

  # add newline chars
  all_chars="${all_chars}\n\r"

  # rename source NotoSans*ttf files to include "Original" in the name
  mv ${ss_translations_repo}/fonts/NotoSansAR-Regular.ttf ${ss_translations_repo}/fonts/NotoSansAR-Regular-Original.ttf
  mv ${ss_translations_repo}/fonts/NotoSansJP-Regular.ttf ${ss_translations_repo}/fonts/NotoSansJP-Regular-Original.ttf
  mv ${ss_translations_repo}/fonts/NotoSansKR-Regular.ttf ${ss_translations_repo}/fonts/NotoSansKR-Regular-Original.ttf
  mv ${ss_translations_repo}/fonts/NotoSansSC-Regular.ttf ${ss_translations_repo}/fonts/NotoSansSC-Regular-Original.ttf
  mv ${ss_translations_repo}/fonts/NotoSansTH-Regular.ttf ${ss_translations_repo}/fonts/NotoSansTH-Regular-Original.ttf

  # slim down font files using characters in all_chars
  pyftsubset ${ss_translations_repo}/fonts/NotoSansAR-Regular-Original.ttf --text="${all_chars}" --output-file=${ss_translations_repo}/fonts/NotoSansAR-Regular.ttf || exit
  pyftsubset ${ss_translations_repo}/fonts/NotoSansJP-Regular-Original.ttf --text="${all_chars}" --output-file=${ss_translations_repo}/fonts/NotoSansJP-Regular.ttf || exit
  pyftsubset ${ss_translations_repo}/fonts/NotoSansKR-Regular-Original.ttf --text="${all_chars}" --output-file=${ss_translations_repo}/fonts/NotoSansKR-Regular.ttf || exit
  pyftsubset ${ss_translations_repo}/fonts/NotoSansSC-Regular-Original.ttf --text="${all_chars}" --output-file=${ss_translations_repo}/fonts/NotoSansSC-Regular.ttf || exit
  pyftsubset ${ss_translations_repo}/fonts/NotoSansTH-Regular-Original.ttf --text="${all_chars}" --output-file=${ss_translations_repo}/fonts/NotoSansTH-Regular.ttf || exit

  # remove original font files
  rm -f ${ss_translations_repo}/fonts/NotoSans*Regular-Original*ttf

  cd -
  deactivate

}

write_version_json() {
  # Writes ${rootfs_overlay}/opt/src/seedsigner/version.json, which SeedSigner OS reads
  # at runtime. A missing or incomplete file makes the app raise at the splash screen,
  # so failures here are fatal.
  # Must run while .git/ and tools/ are still present (delete_unnecessary_files drops them).
  app_dir="${rootfs_overlay}/opt"

  if [ -f "${app_dir}/src/seedsigner/version.json" ]; then
    # Already written: either by an earlier build_image call (--all) or by the caller.
    echo "version.json already present, leaving as-is"
    cat "${app_dir}/src/seedsigner/version.json"
    return
  fi

  if [ ! -f "${app_dir}/tools/write_versionfile.py" ]; then
    echo "ERROR: ${app_dir}/tools/write_versionfile.py not found; cannot write version.json" >&2
    exit 1
  fi

  # In CI the overlay is a bind-mounted host checkout owned by a different uid than the
  # root user in this container. git refuses such repos ("dubious ownership") and
  # version.py silently discards the error, yielding a version.json that bricks boot.
  git config --global --add safe.directory "$(cd "${app_dir}" && pwd)"

  # PYTHONPATH=src is enough; write_versionfile.py's imports are stdlib-only.
  export SEEDSIGNER_OS_BUILDER=1
  (cd "${app_dir}" && PYTHONPATH=src python3 tools/write_versionfile.py) || exit
}

download_app_repo() {
  # remove previous opt seedsigner app repo code if it already exists
  rm -fr ${rootfs_overlay}/opt/
  
  # Download SeedSigner from GitHub and put into rootfs
  
  # check for custom app branch or custom commit. Custom commit takes priority over branch name
  if ! [ -z ${seedsigner_app_repo_commit_id} ]; then
    echo "cloning repo ${seedsigner_app_repo} with commit id ${seedsigner_app_repo_commit_id}"
    git clone --recurse-submodules "${seedsigner_app_repo}" "${rootfs_overlay}/opt/" || exit
    cd ${rootfs_overlay}/opt/
    git reset --hard "${seedsigner_app_repo_commit_id}" || exit
    git submodule update || exit
    cd -
  else
    echo "cloning repo ${seedsigner_app_repo} with branch ${seedsigner_app_repo_branch}"
    git clone --recurse-submodules --depth 1 -b "${seedsigner_app_repo_branch}" "${seedsigner_app_repo}" "${rootfs_overlay}/opt/" || exit
  fi

  check_pinned_dependencies

  compile_translations_and_fonts
}

check_pinned_dependencies() {
  # The app's requirements.txt and this repo's Buildroot packages pin the same libraries twice:
  # requirements.txt governs a desktop or simulator checkout, the Buildroot packages govern the
  # device image. delete_unnecessary_files() then removes requirements.txt from the rootfs, so
  # nothing on the device records what the desktop was built against and a disagreement between
  # the two is invisible until the hardware behaves differently from the simulator.
  #
  # That has already cost real time once: the pins drifted and the image shipped embit 0.8.0, so
  # the wallet had no silent payments at all while every desktop test passed. Fail the build here
  # instead, before spending an hour compiling the wrong thing.
  local requirements="${rootfs_overlay}/opt/requirements.txt"
  if [ ! -f "${requirements}" ]; then
    echo "check_pinned_dependencies: no requirements.txt in the app checkout, skipping"
    return 0
  fi

  local failed=0
  # package-name-in-requirements  buildroot-makefile  make-variable
  while read -r name mk var; do
    [ -z "${name}" ] && continue

    local pinned=""
    local shipped=""
    if [ -f "${requirements}" ]; then
      pinned=$(grep -oE "${name} @ [^ ]*/archive/[0-9a-f]{40}" "${requirements}" | grep -oE "[0-9a-f]{40}$" | head -1 || true)
    fi
    if [ -f "${mk}" ]; then
      shipped=$(grep -oE "^${var} = [0-9a-f]{40}" "${mk}" | grep -oE "[0-9a-f]{40}$" | head -1 || true)
    fi

    if [ -z "${pinned}" ] || [ -z "${shipped}" ]; then
      echo "check_pinned_dependencies: could not read both pins for ${name}, skipping"
      continue
    fi

    if [ "${pinned}" != "${shipped}" ]; then
      echo "ERROR: ${name} is pinned to two different commits."
      echo "  requirements.txt (desktop): ${pinned}"
      echo "  ${mk} (device):             ${shipped}"
      failed=1
    else
      echo "check_pinned_dependencies: ${name} agrees at ${shipped}"
    fi
  done <<EOF
embit ${cur_dir}/external-packages/python-embit/python-embit.mk PYTHON_EMBIT_VERSION
pydnssec-prover ${cur_dir}/external-packages/python-pydnssec-prover/python-pydnssec-prover.mk PYTHON_PYDNSSEC_PROVER_VERSION
EOF

  if [ "${failed}" != "0" ]; then
    echo "Refusing to build an image whose device and desktop pins disagree."
    exit 1
  fi
}

delete_unnecessary_files() {
  # Delete unnecessary files to save space. Runs independently of download_app_repo so
  # that --skip-repo builds (CI, which populates rootfs-overlay/opt itself) are trimmed
  # too. Must run after write_version_json, which needs .git/ and tools/.
  # folders
  rm -rf ${rootfs_overlay}/opt/.github
  rm -rf ${rootfs_overlay}/opt/docker
  rm -rf ${rootfs_overlay}/opt/docs
  rm -rf ${rootfs_overlay}/opt/enclosures
  rm -rf ${rootfs_overlay}/opt/l10n
  rm -rf ${rootfs_overlay}/opt/seedsigner-screenshots
  rm -rf ${rootfs_overlay}/opt/src/seedsigner/resources/seedsigner-translations/.git*
  rm -rf ${rootfs_overlay}/opt/tests
  rm -rf ${rootfs_overlay}/opt/tools
  # files
  rm -rf ${rootfs_overlay}/opt/.git*
  rm -rf ${rootfs_overlay}/opt/docker-compose.yml
  rm -rf ${rootfs_overlay}/opt/LICENSE.md
  rm -rf ${rootfs_overlay}/opt/MANIFEST.in
  rm -rf ${rootfs_overlay}/opt/pyproject.toml
  rm -rf ${rootfs_overlay}/opt/README.md
  rm -rf ${rootfs_overlay}/opt/requirements-raspi.txt
  rm -rf ${rootfs_overlay}/opt/requirements.txt
  rm -rf ${rootfs_overlay}/opt/seedsigner_pubkey.gpg
  rm -rf ${rootfs_overlay}/opt/setup.*

  rm -rf ${rootfs_overlay}/opt/src/seedsigner/resources/seedsigner-translations/LICENSE
  rm -rf ${rootfs_overlay}/opt/src/seedsigner/resources/seedsigner-translations/README.md
  rm -rf ${rootfs_overlay}/opt/src/seedsigner/resources/seedsigner-translations/l10n/**/**/*.po
}

check_target_rootfs() {
  # Assert what the build actually produced, while the target tree still exists.
  #
  # This has to happen here and not against the .img afterwards. The rootfs is an LZ4-compressed
  # initramfs, so running strings over the image finds nothing and reports every library as
  # missing, and ${build_dir} is inside the container and gone by the time a workflow could look.
  #
  # All three of these fail silently rather than loudly when absent, which is why they are worth
  # asserting at all. embit falls back to a pure-Python secp256k1 and is then too slow to sign on a
  # Pi Zero; the DNSSEC validator falls back to pure-Python ECDSA and is roughly 8x slower over a
  # chain; and without pydnssec_prover the wallet simply never offers to check a payment name. Not
  # one of them prints an error, so an image can be quietly worse than the last one.
  local target="${1}/target"
  local failed=0

  if [ ! -d "${target}" ]; then
    echo "check_target_rootfs: no target tree at ${target}, skipping"
    return 0
  fi

  # Look for the package directory and not for a .py file inside it. Buildroot defaults to
  # BR2_PACKAGE_PYTHON3_PYC_ONLY, which compiles every module and then DELETES the source, so
  # validation.py is legitimately absent from a correctly built image. Checking for it failed a
  # build in which the package had downloaded, cross-compiled and installed exactly as intended.
  local pydnssec
  pydnssec=$(ls -d "${target}"/usr/lib/python3*/site-packages/pydnssec_prover 2>/dev/null | head -1 || true)
  if [ -z "${pydnssec}" ] || [ -z "$(ls -A "${pydnssec}" 2>/dev/null)" ]; then
    echo "ERROR: pydnssec_prover is not in the target rootfs; the wallet cannot check a BIP-353 name"
    echo "       looked under ${target}/usr/lib/python3*/site-packages/"
    ls "${target}"/usr/lib/python3*/site-packages/ 2>/dev/null | head -40
    failed=1
  else
    echo "check_target_rootfs: pydnssec_prover at ${pydnssec} ($(ls -A "${pydnssec}" | wc -l) entries)"
  fi

  if ! ls "${target}"/usr/lib/libsecp256k1.so* >/dev/null 2>&1; then
    echo "ERROR: libsecp256k1 is not in the target rootfs; embit would fall back to pure Python"
    ls "${target}"/usr/lib/lib*.so* 2>/dev/null | head -40
    failed=1
  fi

  if ! ls "${target}"/usr/lib/libcrypto.so* >/dev/null 2>&1; then
    echo "ERROR: libcrypto is not in the target rootfs; DNSSEC ECDSA would fall back to pure Python"
    ls "${target}"/usr/lib/lib*.so* 2>/dev/null | head -40
    failed=1
  fi

  if [ "${failed}" != "0" ]; then
    echo "Refusing to publish an image that is missing what it is supposed to carry."
    exit 1
  fi

  echo "check_target_rootfs: pydnssec_prover, libsecp256k1 and libcrypto are all present"
}

build_image() {
  # arguments: $1 - config name, $2 clean/no-clean - allows for, $3 skip-repo

  # Variables
  config_name="${1:-pi0}"
  config_dir="./${config_name}"
  rootfs_overlay="./rootfs-overlay"
  config_file="${config_dir}/configs/pi0"
  build_dir="${cur_dir}/../output"
  build2_dir="${cur_dir}/../../output"
  image_dir="${cur_dir}/../images"

  if [ ! -d "${config_dir}" ]; then
    # config does not exists
    echo "config ${config_name} not found"
    exit 1
  fi
  
  if [ "${2}" != "no-clean" ]; then
  
    # remove previous build dir
    rm -rf "${build_dir}"
    mkdir -p "${build_dir}"
    
  fi
  
  if [ "${3}" != "skip-repo" ]; then
    download_app_repo
  fi

  # Both run unconditionally so --skip-repo builds get the same treatment. Order
  # matters: write_version_json needs the .git/ and tools/ that the other removes.
  write_version_json
  delete_unnecessary_files

  # Setup external tree
  #make BR2_EXTERNAL="../${config_dir}/" O="${build_dir}" -C ./buildroot/ #2> /dev/null > /dev/null

  PATH="/usr/lib/ccache:${PATH}" make BR2_EXTERNAL="../${config_dir}/" O="${build_dir}" -C ./buildroot/ ${config_name}_defconfig
  cd "${build_dir}"
  PATH="/usr/lib/ccache:${PATH}" make

  check_target_rootfs "${build_dir}"
  
  # if successful, mv seedsigner_os.img image to /images
  # rename to image to include branch name and config name, then compress
  
  # A branch name goes into the FILENAME, so any slash in it would be read as a
  # directory that does not exist and the final mv fails after a full build has
  # already succeeded. Seen for real with --app-branch=feat/bip352-silent-payments.
  # Flatten the separators rather than rejecting the branch.
  branch_slug="$(echo "${seedsigner_app_repo_branch}" | tr '/ ' '__')"
  seedsigner_os_image_output="${image_dir}/seedsigner_os.${branch_slug}.${config_name}.img"
  if ! [ -z ${seedsigner_app_repo_commit_id} ]; then
    # use commit id instead of branch name if it is set
    seedsigner_os_image_output="${image_dir}/seedsigner_os.${seedsigner_app_repo_commit_id}.${config_name}.img"
  fi
  
  if [ -f "${build_dir}/images/seedsigner_os.img" ] && [ -d "${image_dir}" ]; then
    mv -f "${build_dir}/images/seedsigner_os.img" "${seedsigner_os_image_output}"
    sha256sum "${seedsigner_os_image_output}"
  fi
  
  cd - > /dev/null # return to previous working directory quietly
}

###
### Gather Arguments passed into build.sh script
###

VALID_ARGUMENTS=$# # Returns the count of arguments that are in short or long options

if [ "$VALID_ARGUMENTS" -eq 0 ]; then
  help
fi

PARAMS=""
ARCH_CNT=0
while (( "$#" )); do
  case "$1" in
  -a|--all)
    ALL_FLAG=0; ((ARCH_CNT=ARCH_CNT+1)); shift
    ;;
  -h|--help)
    HELP_FLAG=0; shift
    ;;
  --pi0)
    PI0_FLAG=0; ((ARCH_CNT=ARCH_CNT+1)); shift
    ;;
  --pi2)
    PI2_FLAG=0; ((ARCH_CNT=ARCH_CNT+1)); shift
    ;;
  --pi02w)
    PI02W_FLAG=0; ((ARCH_CNT=ARCH_CNT+1)); shift
    ;;
  --pi4)
    PI4_FLAG=0; ((ARCH_CNT=ARCH_CNT+1)); shift
    ;;
  --lafrite)
    LAFRITE_FLAG=0; ((ARCH_CNT=ARCH_CNT+1)); shift
    ;;
  --no-clean)
    NOCLEAN=0; shift
    ;;
  --skip-repo)
    SKIPREPO=0; shift
    ;;
  --no-op)
    NO_OP=0; shift
    ;;
  --dev)
    DEVBUILD=0; shift
    ;;
  --smartcard)
    SMARTCARD=0; shift
    ;;
  --app-repo=*)
    APP_REPO=$(echo "${1}" | cut -d "=" -f2-); shift
    ;;
  --app-branch=*)
    APP_BRANCH=$(echo "${1}" | cut -d "=" -f2-); shift
    ;;
  --app-commit-id=*)
    APP_COMMITID=$(echo "${1}" | cut -d "=" -f2-); shift
    ;;
  -*|--*=) # unsupported flags
    echo "Error: Unsupported flag $1" >&2
    help
    exit 1
    ;;
  *) # unsupported flags
    echo "Error: Unsupported argument $1" >&2
    help
    exit 1
    ;;
  esac
done

# When no arguments, display help
if ! [ -z ${HELP_FLAG} ]; then
  help
fi

# Only allow 1 architecture related argument flag
if [ $ARCH_CNT -gt 1 ]; then
  echo "Invalid number of architecture arguments" >&2
  exit 3
fi

# if no-op then hang endlessly
if ! [ -z $NO_OP ]; then
  tail_endless
  exit 0
fi

# Check for --no-clean argument to pass clean/no-clean to build_image function
if [ -z $NOCLEAN ]; then
  CLEAN_ARG="clean"
else
  CLEAN_ARG="no-clean"
fi

# Check for --no-clean argument to pass clean/no-clean to build_image function
if ! [ -z $SKIPREPO ]; then
  SKIPREPO_ARG="skip-repo"
else
  SKIPREPO_ARG="no-skip-repo"
fi

echo $SKIPREPO_ARG

# Check for --dev argument to pass to build_image function
DEVARG=""
if ! [ -z $DEVBUILD ]; then
  DEVARG="-dev"
fi

# Check for --smartcard argument to pass to build_image function
SMARTCARDARG=""
if ! [ -z $SMARTCARD ]; then
  SMARTCARDARG="-smartcard"
fi

# check for custom app repo
if ! [ -z ${APP_REPO} ]; then
  seedsigner_app_repo="${APP_REPO}"
fi

# check for custom app branch
if ! [ -z ${APP_BRANCH} ]; then
  seedsigner_app_repo_branch="${APP_BRANCH}"
fi

# check for custom app branch
if ! [ -z ${APP_COMMITID} ]; then
  seedsigner_app_repo_commit_id="${APP_COMMITID}"
fi

###
### Run build_image function based on input arguments
###

# Build All Architectures
if ! [ -z ${ALL_FLAG} ]; then
  build_image "pi0${SMARTCARDARG}${DEVARG}" "clean" "${SKIPREPO_ARG}"
  build_image "pi02w${SMARTCARDARG}${DEVARG}" "clean" "skip-repo"
  build_image "pi2${SMARTCARDARG}${DEVARG}" "clean" "skip-repo"
  build_image "pi4${SMARTCARDARG}${DEVARG}" "clean" "skip-repo"
fi

# Build only for pi0, pi0w, and pi1
if ! [ -z ${PI0_FLAG} ]; then
  build_image "pi0${SMARTCARDARG}${DEVARG}" "${CLEAN_ARG}" "${SKIPREPO_ARG}"
fi

# Build only for pi2
if ! [ -z ${PI2_FLAG} ]; then
  build_image "pi2${SMARTCARDARG}${DEVARG}" "${CLEAN_ARG}" "${SKIPREPO_ARG}"
fi

# build for pi02w
if ! [ -z ${PI02W_FLAG} ]; then
  build_image "pi02w${SMARTCARDARG}${DEVARG}" "${CLEAN_ARG}" "${SKIPREPO_ARG}"
fi

# build for pi4
if ! [ -z ${PI4_FLAG} ]; then
  build_image "pi4${SMARTCARDARG}${DEVARG}" "${CLEAN_ARG}" "${SKIPREPO_ARG}"
fi

# build for La Frite AML-S805X-AC. Only the smartcard configs exist for this
# board, so --lafrite without --smartcard has no config dir to build from.
if ! [ -z ${LAFRITE_FLAG} ]; then
  build_image "lafrite${SMARTCARDARG}${DEVARG}" "${CLEAN_ARG}" "${SKIPREPO_ARG}"
fi

exit 0
