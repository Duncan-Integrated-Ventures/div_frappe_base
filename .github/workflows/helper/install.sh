#!/bin/bash
set -e

install_wkhtmltopdf() {
	wget -O /tmp/wkhtmltox.deb https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb
	sudo apt install /tmp/wkhtmltox.deb
}

cd ~ || exit

sudo apt update
sudo apt remove mysql-server mysql-client
sudo apt install -y libcups2-dev redis-server mariadb-client cron supervisor
sudo apt install -y unixodbc-dev build-essential gcc libc-dev libdmtx0t64

if [ "$DB" == "mariadb" ]; then
	mariadb --host 127.0.0.1 --port 3306 -u root -p123 -e "SET GLOBAL character_set_server = 'utf8mb4'"
	mariadb --host 127.0.0.1 --port 3306 -u root -p123 -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"
fi

pip install frappe-bench

# Clone frappe_devutils to bootstrap: we need its scripts/setup.py helpers to
# resolve the frappe branch from the Site Configuration, and we need the app
# itself present so bench auto-discovers its custom commands (list-configs,
# setup-apps, new-site-from-config, import-fixtures) once it's installed.
git clone "https://${GITHUB_TOKEN}@github.com/npxladmin/frappe_devutils" \
	--branch develop ~/frappe_devutils

# Resolve the frappe branch from the Site Configuration so bench init uses the right version.
FRAPPE_BRANCH=$(python3 - <<EOF
import os, sys
sys.path.insert(0, os.path.expanduser("~/frappe_devutils"))
from frappe_devutils.scripts.setup import FrappeClient, app_folder
doc = FrappeClient().get_site_configuration("${SITE_CONFIG}")
for r in doc["application_records"]:
    if app_folder(r) == "frappe":
        print(r.get("git_branch", "version-15"))
        sys.exit(0)
print("version-15")
EOF
)

git clone "https://github.com/frappe/frappe" --branch "$FRAPPE_BRANCH" --depth 1 ~/frappe
bench init --skip-assets --frappe-path ~/frappe --python "$(which python)" frappe-bench

cd frappe-bench || exit
echo "Changed directory to frappe-bench"
source env/bin/activate
echo "Using bench's env"

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile
echo "Configured Procfile"

install_wkhtmltopdf & wkpid=$!

# frappe_devutils has to be installed into bench BEFORE `bench setup-apps` can
# run (bench only discovers custom commands from installed apps). When the PR
# under test is frappe_devutils itself, install it from GITHUB_WORKSPACE so the
# PR's code is what runs; otherwise install from the bootstrap clone.
if [ "${APP_UNDER_TEST}" = "frappe_devutils" ]; then
	bench get-app --skip-assets frappe_devutils "${GITHUB_WORKSPACE}"
else
	bench get-app --skip-assets frappe_devutils ~/frappe_devutils
fi

# Fetch/update every app in the Site Configuration. `--local-app` points the
# app under test at GITHUB_WORKSPACE so the PR's code is what gets installed.
# `--test` passes --skip-assets to every `bench get-app`.
bench setup-apps \
	--config "${SITE_CONFIG}" \
	--test \
	--local-app "${APP_UNDER_TEST}:${GITHUB_WORKSPACE}"

# Create the test site and import fixtures.
bench new-site-from-config \
	--config "${SITE_CONFIG}" \
	--site-name test_site \
	--import-fixtures

wait $wkpid
echo "Wkhtmltox installation, bench, apps, and site setup complete"

bench start &>> ~/frappe-bench/bench_start.log &
echo "Bench started"
