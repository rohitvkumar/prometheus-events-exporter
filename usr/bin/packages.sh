#!/bin/sh

set -eou

# Install essentials
apk --update add curl python py-pip runit tar zlib gzip

# Install temporary packages
# apk add --virtual build-dependencies build-base git openssl

# Install required python packages
pip install --upgrade pip
pip install requests bottle kafka-python prometheus_client

#cleanup
#apk del build-dependencies
rm -rf /var/cache/apk/*
