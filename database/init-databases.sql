\getenv db_password POSTGRES_PASSWORD

CREATE ROLE users_service LOGIN PASSWORD :'db_password';
CREATE DATABASE users_db OWNER users_service;
REVOKE CONNECT ON DATABASE users_db FROM PUBLIC;
GRANT CONNECT ON DATABASE users_db TO users_service;

CREATE ROLE products_service LOGIN PASSWORD :'db_password';
CREATE DATABASE products_db OWNER products_service;
REVOKE CONNECT ON DATABASE products_db FROM PUBLIC;
GRANT CONNECT ON DATABASE products_db TO products_service;

CREATE ROLE inventory_service LOGIN PASSWORD :'db_password';
CREATE DATABASE inventory_db OWNER inventory_service;
REVOKE CONNECT ON DATABASE inventory_db FROM PUBLIC;
GRANT CONNECT ON DATABASE inventory_db TO inventory_service;

CREATE ROLE orders_service LOGIN PASSWORD :'db_password';
CREATE DATABASE orders_db OWNER orders_service;
REVOKE CONNECT ON DATABASE orders_db FROM PUBLIC;
GRANT CONNECT ON DATABASE orders_db TO orders_service;

CREATE ROLE payments_service LOGIN PASSWORD :'db_password';
CREATE DATABASE payments_db OWNER payments_service;
REVOKE CONNECT ON DATABASE payments_db FROM PUBLIC;
GRANT CONNECT ON DATABASE payments_db TO payments_service;

CREATE ROLE notifications_service LOGIN PASSWORD :'db_password';
CREATE DATABASE notifications_db OWNER notifications_service;
REVOKE CONNECT ON DATABASE notifications_db FROM PUBLIC;
GRANT CONNECT ON DATABASE notifications_db TO notifications_service;
