FROM php:8.3-apache

RUN apt-get update \
 && apt-get install -y --no-install-recommends libzip-dev libpq-dev \
 && docker-php-ext-install zip pdo_pgsql \
 && rm -rf /var/lib/apt/lists/*

RUN a2enmod rewrite headers remoteip \
 && sed -ri 's/AllowOverride None/AllowOverride All/' /etc/apache2/apache2.conf \
 && { echo 'memory_limit=512M'; echo 'max_execution_time=600'; echo 'expose_php=Off'; } \
      > /usr/local/etc/php/conf.d/app.ini \
 && { echo 'RemoteIPHeader X-Forwarded-For'; echo 'RemoteIPInternalProxy 10.0.0.0/8 172.16.0.0/12'; } \
      > /etc/apache2/conf-enabled/remoteip.conf \
 && echo 'ServerTokens Prod' > /etc/apache2/conf-enabled/tokens.conf

COPY . /var/www/html/
RUN rm -rf /var/www/html/docker /var/www/html/.git \
 && mkdir -p /var/www/html/data && chown -R www-data:www-data /var/www/html/data
