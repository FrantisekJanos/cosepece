FROM php:8.3-apache

RUN apt-get update \
 && apt-get install -y --no-install-recommends libzip-dev libpq-dev libpng-dev libfreetype6-dev libonig-dev libsqlite3-dev unzip \
 && docker-php-ext-configure gd --with-freetype \
 && docker-php-ext-install zip pdo_pgsql pdo_mysql pdo_sqlite mbstring gd \
 && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer
WORKDIR /var/www/html
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-interaction --prefer-dist --no-scripts --no-autoloader

RUN a2enmod rewrite headers remoteip \
 && sed -ri 's/AllowOverride None/AllowOverride All/' /etc/apache2/apache2.conf \
 && sed -ri 's!/var/www/html!/var/www/html/public!g' /etc/apache2/sites-available/*.conf \
 && { echo 'memory_limit=256M'; echo 'max_execution_time=60'; echo 'expose_php=Off'; \
      echo 'display_errors=Off'; echo 'log_errors=On'; echo 'error_log=/dev/stderr'; \
      echo 'post_max_size=2M'; echo 'upload_max_filesize=0'; echo 'file_uploads=Off'; \
      echo 'session.use_strict_mode=1'; } \
      > /usr/local/etc/php/conf.d/app.ini \
 && { echo '<IfModule mpm_prefork_module>'; echo '  StartServers 4'; echo '  MinSpareServers 4'; echo '  MaxSpareServers 10'; \
      echo '  MaxRequestWorkers 40'; echo '  MaxConnectionsPerChild 2000'; echo '</IfModule>'; \
      echo 'Timeout 30'; echo 'ServerSignature Off'; echo 'TraceEnable Off'; } \
      > /etc/apache2/conf-enabled/limits.conf \
 && { echo 'RemoteIPHeader X-Forwarded-For'; echo 'RemoteIPInternalProxy 10.0.0.0/8 172.16.0.0/12'; } \
      > /etc/apache2/conf-enabled/remoteip.conf \
 && echo 'ServerTokens Prod' > /etc/apache2/conf-enabled/tokens.conf

COPY . .
RUN composer dump-autoload --no-dev --classmap-authoritative --no-scripts \
 && php artisan package:discover \
 && mkdir -p data storage/framework/cache/data storage/framework/sessions storage/framework/views storage/logs bootstrap/cache \
 && chown -R www-data:www-data data storage bootstrap/cache

# Migrace se provede jednou před startem webu. Scheduler čeká na zdravý web.
CMD ["sh", "-c", "su -s /bin/sh www-data -c 'php artisan migrate --force' && exec apache2-foreground"]
