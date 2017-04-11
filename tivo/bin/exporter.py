#!/usr/bin/env python

import argparse
import atexit
import json
from kafka import KafkaConsumer
import logging
from logging.handlers import RotatingFileHandler
import os
from prometheus_client import start_http_server, Gauge, Counter
import re
import sys
import traceback
import time
import threading


VERBOSE = False
SIMULATED = False
INTERVAL_S = 15
CACHE_CLEANUP_INTERVAL_S = 300


gauges = {}
counters = {}
lru_cache_active = dict()
lru_cache_inactive = dict()

def normalize_label(token):
    return re.sub(r'[^a-zA-Z0-9_:]', '_', token)

def flatten_attrs(attrs):
    str = ""
    for k, v in attrs.items():
        if str:
            str += " | "
        str += k + ":" + v
    return str

def transformEventToPrometheusMetric(eventStr, drop_list, verbose):
    try:
        if not eventStr:
            return None
        
        event = json.loads(eventStr)
        
        if not event.has_key('type') or not event.has_key('service'):
            return None
        
        if drop_list and event.get('type') in drop_list:
            return None
        
        name = event.get('type')
        service = event.get('service')
        labels = {}
        
        # collect labels
        for k, v in event.items():
            if k in ["attrs", "type", "tsMS", "details", "service"]:
                continue
            labels[normalize_label(k)] = v
        
        labels["attrs"] = ""
        num_attrs = {}
        other_attrs = {}
        if event.has_key("attrs"):
            for k, v in event.get('attrs').items():
                met_name = normalize_label('{0}_{1}_{2}'.format(service, name, k))
                try:
                    num_attrs[k] = float(v)
                except ValueError as e:
                    other_attrs[k] = v
            '''
            TODO: Enable this after parallelizing the exporting
            if other_attrs:
                labels["attrs"] = flatten_attrs(other_attrs)
            '''
                
        # Emit one counter per event        
        met_name = normalize_label('{0}_{1}'.format(service, name))
        process_counter(met_name, labels, 1)
        
        # Emit floating point attributes as gauges.
        for k, v in num_attrs.items():
            met_name = normalize_label('{0}_{1}_{2}'.format(service, name, k))
            process_guage(met_name, labels, v)
                
    except Exception as e:
        logger.exception("Exception while transforming.")
            
    
def process_guage(name, labels, value):
    try:
        if name not in gauges:
            gauges[name] = Gauge(name, '', tuple(labels.keys()))
        
        if labels:
            tup = tuple(labels.values())
            key = (name, tup, 'g')
            lru_cache_active[key] = 1
            gauges[name].labels(*tup).set(value)
            if key in lru_cache_inactive:
                del lru_cache_inactive[key]
        else:
            gauges[name].set(value)
    except Exception as e:
        logger.error("Gauge Exception: {0} - Name: {1} Labels: {2} Value: {3}".format(e, name, json.dumps(sorted(labels.keys())), value))
        logger.error("Current labels: {0}".format(json.dumps(sorted(counters[name]._labelnames))))
        
def process_counter(name, labels, value):
    try:
        if name not in counters:
            counters[name] = Counter(name, '', tuple(labels.keys()))
        
        if labels:
            tup = tuple(labels.values())
            key = (name, tup, 'c')
            lru_cache_active[key] = 1
            counters[name].labels(*tup).inc(value)
            if key in lru_cache_inactive:
                del lru_cache_inactive[key]
        else:
            counters[name].inc(value)
    except Exception as e:
        logger.error("Counter Exception: {0} - Name: {1} Labels: {2} Value: {3}".format(e, name, json.dumps(sorted(labels.keys())), value))
        logger.error("Current labels: {0}".format(json.dumps(sorted(counters[name]._labelnames))))

def read_topic(consumer, drop_list):
    logger.debug("Initiate reading events.")
    
    start_time = time.time()
    count = 0
    for message in consumer:
        transformEventToPrometheusMetric(message.value, drop_list, VERBOSE)
        count += 1
        if (time.time() - start_time) > INTERVAL_S:
            logger.info("Processed {0} events in {1} seconds, now exit loop.".format(count, INTERVAL_S))
            break

def reset_lru():
    if time.time() - last_clean < CACHE_CLEANUP_INTERVAL_S:
        return
    for lru, val in lru_cache_inactive.items():
        try:
            name = lru[0]
            tup = lru[1]
            if lru[2] == 'g':
                gauges[name].remove(*tup)
                logger.debug("Reset gauge: {0}:{1}".format(name, tup))
            else:
                counters[name].remove(*tup)
                logger.debug("Reset counter: {0}:{1}".format(name, tup))
        except Exception as e:
            logger.error("Error processing lru: {0}".format(e))
    lru_cache_inactive.clear()
    global lru_cache_inactive
    global lru_cache_active
    lru_cache_inactive = lru_cache_active
    lru_cache_active = dict()
    global last_clean
    last_clean = time.time()
    
def wait_for_threads():
    seconds = 0
    while threading.active_count() != 0:
        print 'Waiting for threads to exit.'
        time.sleep(1)
        seconds += 1
        if seconds == 10:
            sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="Export events to prometheus.")
    
    parser.add_argument("-v", "--verbose", help="Verbose output.", action="store_true")
    parser.add_argument("-s", "--simulated", help="Debugging only - no changes will be made to cluster.", action="store_true")
    parser.add_argument("-b", "--broker", help="Kafka bootstrap broker", metavar="hostname", required=True)
    parser.add_argument("-p", "--port", help="Kafka bootstrap broker port", metavar="port", type=int, default=9092)
    parser.add_argument("-d", "--drop-list", help="Drop this metric", metavar="TERM", action="append")
    
    args = parser.parse_args()
    
    global VERBOSE
    global SIMULATED
    
    VERBOSE = args.verbose
    SIMULATED = args.simulated
    
    if VERBOSE:
        logger.setLevel(logging.DEBUG)
        
    logger.debug(args)
    
    bootstrap = ['{0}:{1}'.format(args.broker, args.port)]
    
    logger.info("Preparing to listen for events from {}".format(bootstrap))
    
    try:
        timeout_ms = INTERVAL_S * 1000
        client = KafkaConsumer("events",
                     enable_auto_commit=(not SIMULATED),
                     group_id="events-metrics-prometheus-exporter-{0}-{1}".format(args.broker, args.port),
                     auto_offset_reset='latest',
                     bootstrap_servers=bootstrap,
                     consumer_timeout_ms=timeout_ms,
                     api_version=(0,10))
        start_http_server(9800)
        
        global last_clean
        last_clean = 0
        while True:
            read_topic(client, args.drop_list)
            reset_lru()
                
    except KeyboardInterrupt as e:
        logger.info("Stopped")
        client.close()

if __name__ == "__main__":
    
    log_file = "/TivoData/Log/exporter/exporter.log"
    if not os.path.exists(os.path.dirname(log_file)):
        os.makedirs(os.path.dirname(log_file))
    global logger
    logger = logging.getLogger("exporter")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_file,
                                  maxBytes=1024*1024*1024,
                                  backupCount=5)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    atexit.register(wait_for_threads)
    main()
