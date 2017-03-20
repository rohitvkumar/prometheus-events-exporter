#!/bin/sh

set -eoux

curl -s "http://localhost:50000/Properties?environment=${ENVIRONMENT_NAME}&service=${SERVICE_NAME}" | \
sort > "/${SERVICE_NAME}.properties" || \
( sleep 60 && exit 1 )

if [ -f "/${SERVICE_NAME}.properties.md5" ]
then
    md5sum -csw "/${SERVICE_NAME}.properties.md5" || \
    ( echo "Service properties changed. Send signal." && sv alarm admin )
    echo "Service properties have not changed."
else
    echo "Storing md5 file for the properties."
    md5sum "/${SERVICE_NAME}.properties" > "/${SERVICE_NAME}.properties.md5"
fi
sleep 60