# Sourced by the operator scripts: `site_env KEY …` reads site.env at the root
# of the repository (another file: SITE_ENV=<path>) and stops the script when
# the file or one of the keys it names is missing. See site.env.example.
site_env() {
    local root file key
    root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    file=${SITE_ENV:-$root/site.env}
    if [ ! -f "$file" ]; then
        echo "$file is missing: copy site.env.example to site.env and fill it in" >&2
        exit 1
    fi
    # shellcheck disable=SC1090
    . "$file"
    for key in "$@"; do
        if [ -z "${!key:-}" ]; then
            echo "$key is not set in $file (see site.env.example)" >&2
            exit 1
        fi
    done
}
