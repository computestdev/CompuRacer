#!/bin/bash

export DEBIAN_FRONTEND=noninteractive

apt-get -qy update
apt-get -qy dist-upgrade

apt-get -qy install mariadb-server nginx python3-pip python3-flask python3-mysql.connector uwsgi uwsgi-plugin-python3 

python3 -m pip install PyMySQL timeout-decorator

cat > /etc/nginx/sites-available/default << EOF
server {
    listen 80 default_server;

    add_header Cache-Control 'no-store, no-cache';

    location / {
        include uwsgi_params;
        uwsgi_pass unix:/tmp/app.sock;
        
        # when a client closes the connection then keep the channel to uwsgi open. Otherwise uwsgi throws an IOError
        uwsgi_ignore_client_abort on;
    }
}
EOF

cat > /etc/systemd/system/app.service << EOF
[Unit]
Description=uWSGI instance to serve our app
After=network.target

[Service]
User=vagrant
Group=www-data
WorkingDirectory=/vagrant/src/
ExecStart=/usr/bin/uwsgi --ini app.ini

[Install]
WantedBy=multi-user.target
EOF
sudo mkdir -p /var/log/uwsgi
sudo chmod 777 /var/log/uwsgi

cat > /tmp/database.sql << EOF
CREATE DATABASE IF NOT EXISTS voucher;
DROP USER IF EXISTS 'voucher'@'localhost';
CREATE USER IF NOT EXISTS 'voucher'@'localhost' IDENTIFIED BY 'HaiKooLePooxi9uway6oa8Ieroh1hoiw';
GRANT ALL PRIVILEGES ON voucher.* TO 'voucher'@'localhost';

USE voucher;

DROP TABLE IF EXISTS vouchers;
DROP TABLE IF EXISTS vouchers_multi;

CREATE TABLE IF NOT EXISTS vouchers (code VARCHAR(20));
CREATE TABLE IF NOT EXISTS vouchers_multi (code VARCHAR(20), count INT(4));

TRUNCATE TABLE vouchers;
TRUNCATE TABLE vouchers_multi;
EOF

systemctl enable app
systemctl kill app

sleep 2

mariadb < /tmp/database.sql

systemctl start app
systemctl restart nginx

echo "Done!"
