FROM php:8.3-apache

RUN apt-get update \
 && apt-get install -y --no-install-recommends libzip-dev libpq-dev libpng-dev libfreetype6-dev \
 && docker-php-ext-configure gd --with-freetype \
 && docker-php-ext-install zip pdo_pgsql gd \
 && rm -rf /var/lib/apt/lists/*

RUN a2enmod rewrite headers remoteip \
 && sed -ri 's/AllowOverride None/AllowOverride All/' /etc/apache2/apache2.conf \
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

# Veřejné kopie videí pro Instagram (služba reels, volume reelsmedia připojený jen pro čtení mimo kořen webu):
# /reels-media/<token>.mp4, nic jiného (výpis složky, jiné přípony, PHP) se nevydá.
RUN { echo 'Alias /reels-media /srv/reels-media/public'; \
      echo '<Directory /srv/reels-media/public>'; \
      echo '  Options -Indexes'; echo '  AllowOverride None'; echo '  Require all denied'; \
      echo '  <FilesMatch "^[A-Za-z0-9_-]+\.mp4$">'; echo '    Require all granted'; \
      echo '    ForceType video/mp4'; echo '  </FilesMatch>'; \
      echo '  <IfModule php_module>'; echo '    php_admin_flag engine off'; echo '  </IfModule>'; \
      echo '  Header always set X-Content-Type-Options "nosniff"'; \
      echo '  Header always set X-Robots-Tag "noindex"'; \
      echo '</Directory>'; } \
      > /etc/apache2/conf-enabled/reels-media.conf

COPY . /var/www/html/
RUN rm -rf /var/www/html/docker /var/www/html/.git \
 && mkdir -p /var/www/html/data && chown -R www-data:www-data /var/www/html/data
