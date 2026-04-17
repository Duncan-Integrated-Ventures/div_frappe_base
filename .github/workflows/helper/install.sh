#!/bin/bash
set -e

install_wkhtmltopdf() {
	wget -O /tmp/wkhtmltox.deb https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb
	sudo apt install /tmp/wkhtmltox.deb
}

init_frappe() {
	local branch
	while read -r branch; do
		git clone "https://github.com/frappe/frappe" --branch "$branch" --depth 1
		bench init --skip-assets --frappe-path ~/frappe --python "$(which python)" frappe-bench
	done
}

get_app() {
	local line
	# getting app works for --branch tag or branch, but not hash; need to get app at tag then pull fetch and checkout hash if we switch to apps.json
	while read -r line; do
		local app url git
		read -r app url git <<< "$line"
		echo "Installing $app with branch $git from $url"
		if [[ "$url" == *"client-url"* ]]; then
			repo_path="${url#https://github.com/}"
			auth_url="https://${BOT_TOKEN}@github.com/${repo_path}"
			bench get-app "$app" "$auth_url" --branch "$git" --skip-assets
		else
			bench get-app "$app" "$url" --branch "$git" --skip-assets
		fi
	done
}

cd ~ || exit
sudo apt update
sudo apt remove mysql-server mysql-client
sudo apt install -y libcups2-dev redis-server mariadb-client cron supervisor
sudo apt install -y unixodbc-dev build-essential gcc libc-dev libdmtx0t64 # For pyodbc, python dependencies

if [ "$DB" == "mariadb" ];then
	mariadb --host 127.0.0.1 --port 3306 -u root -p123 -e "SET GLOBAL character_set_server = 'utf8mb4'"
	mariadb --host 127.0.0.1 --port 3306 -u root -p123 -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"
fi

pip install frappe-bench

git clone "https://${BOT_TOKEN}@github.com/npxladmin/frappe_devutils" --branch version-15
cp frappe_devutils/frappe_devutils/tests/app_versions.json ~/app_versions.json

jq -r '.frappe.git' app_versions.json | init_frappe

cd frappe-bench || exit
echo "Changed directory to frappe-bench"
source env/bin/activate
echo "Using bench's env"

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile
echo "Configured redis ports"

install_wkhtmltopdf & wkpid=$!

jq -r --arg app "$APP_UNDER_TEST" 'to_entries[] | select(.key != "frappe" and .key != $app) | .key + " " + .value.url + " " + .value.git' ~/app_versions.json | get_app
bench get-app "$APP_UNDER_TEST" "$GITHUB_WORKSPACE" --skip-assets
if [ "${APP_UNDER_TEST}" != "frappe_devutils" ]; then
	bench get-app frappe_devutils ~/frappe_devutils --skip-assets
fi

bench setup requirements --python --dev

wait $wkpid
echo "Bench dependency setup and wkhtmltox installation complete"

bench start &>> ~/frappe-bench/bench_start.log & echo "Bench started"
