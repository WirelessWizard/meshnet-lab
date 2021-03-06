#!/usr/bin/env python3

import random
import datetime
import argparse
import subprocess
import math
import time
import sys
import os
import re


def eprint(s):
    sys.stderr.write(s + '\n')

def exec(cmd, detach=False):
    rc = 0

    if args.verbosity == 'verbose':
        if detach:
            rc = os.system('{} &'.format(cmd))
        else:
            rc = os.system('{}'.format(cmd))
    elif args.verbosity == 'normal':
        if detach:
            rc = os.system('{} > /dev/null &'.format(cmd))
        else:
            rc = os.system('{} > /dev/null'.format(cmd))
    elif args.verbosity == 'quiet':
        if detach:
            rc = os.system('{} > /dev/null 2>&1 &'.format(cmd))
        else:
            rc = os.system('{} > /dev/null 2>&1'.format(cmd))
    else:
        eprint('Abort, invalid verbosity: {}'.format(args.verbosity))
        exit(1)

    if rc != 0:
        eprint('Abort, command failed: {}'.format(cmd))
        #todo: kill routing programs!
        #print('Cleanup done')
        exit(1)

# get time in milliseconds
def millis():
    return int((datetime.datetime.utcnow() - datetime.datetime(1970, 1, 1)).total_seconds() * 1000)

# get system load from uptime command
# average of the last 1, 5 and 15 minutes
def get_load_average():
    p = subprocess.Popen(['uptime'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (out, err) = p.communicate()
    t = out.decode().split('load average:')[1].split(',')
    return (float(t[0]), float(t[1]), float(t[2]))

# get random unique pairs
def get_random_samples(items, npairs):
    samples = {}
    i = 0

    while i < (npairs * 4) and len(samples) != npairs:
        i += 1
        e1 = random.choice(items)
        e2 = random.choice(items)
        if e1 == e2:
            continue
        key = '{}-{}'.format(e1, e2)
        if key not in samples:
            samples[key] = (e1, e2)

    return samples.values()

# get IPv6 address, use fe80:: address as fallback
# TODO: return IPv6 address of the broadest scope in general
def get_ipv6_address(nsname, interface):
    lladdr = None
    # print only IPv6 addresses
    output = os.popen('ip netns exec "{}" ip -6 addr list dev {}'.format(nsname, interface)).read()
    for line in output.split('\n'):
        if 'inet6 ' in line:
            addr = line.split()[1].split('/')[0]
            if addr.startswith('fe80'):
                lladdr = addr
            else:
                return addr

    return lladdr

def get_mac_address(nsname, interface):
    # print only MAC address
    output = os.popen('ip netns exec "{}" ip -0 addr list dev {}'.format(nsname, interface)).read()
    for line in output.split('\n'):
        if 'link/ether ' in line:
            return line.split()[1]

    return None

class PingResult:
    transmitted = 0
    received = 0
    rtt_min = 0.0
    rtt_max = 0.0
    rtt_avg = 0.0

    def __init__(self, transmitted = 0, received = 0, rtt_min = 0.0, rtt_max = 0.0, rtt_avg = 0.0):
        self.transmitted = transmitted
        self.received = received
        self.rtt_min = rtt_min
        self.rtt_max = rtt_max
        self.rtt_avg = rtt_avg

numbers_re = re.compile('[^0-9.]+')

def parse_ping(output):
    ret = PingResult()
    for line in output.split('\n'):
        if 'packets transmitted' in line:
            toks = numbers_re.split(line)
            ret.transmitted = int(toks[0])
            ret.received = int(toks[1])
        if line.startswith('rtt min/avg/max/mdev'):
            toks = numbers_re.split(line)
            ret.rtt_min = float(toks[1])
            ret.rtt_avg = float(toks[2])
            ret.rtt_max = float(toks[3])
            #ret.rtt_mdev = float(toks[4])

    return ret

'''
Add a CSV header if the target file is empty or
extend existing header (for added data outside of this script)
'''
def add_csv_header(file, header):
    pos = file.tell()

    if pos == 0:
        # empty file => add header
        file.write(header)

    if pos > 0 and pos < len(header):
        # non-empty files but cannot be our header => assume existing header and extend it
        file.seek(0)
        content = file.read()
        if content.count('\n') == 1:
            file.seek(0)
            file.truncate()
            lines = content.split('\n')
            file.write(lines[0] + args.csv_delimiter + header)
            file.write(lines[1])

def run_test(nsnames, interface, path_count = 10, test_duration_ms = 1000, wait_ms = 0, outfile = None):
    ping_deadline=1
    ping_count=1
    processes = []

    startup_ms = millis()

    pairs_beg_ms = millis()
    pairs = list(get_random_samples(nsnames, path_count))
    pairs_end_ms = millis()

    ts_beg_beg_ms = millis()
    ts_beg = get_traffic_statistics(nsnames)
    ts_beg_end_ms = millis()

    if args.verbosity != 'quiet':
        print('interface: {}, test duration: {}ms, pairs generation time: {}ms, traffic measurement time: {}ms'.format(
            interface,
            test_duration_ms,
            (pairs_end_ms - pairs_beg_ms),
            (ts_beg_end_ms - ts_beg_beg_ms)
        ))

        if wait_ms > 0:
            print('wait for {} seconds for pings to start.'.format(wait_ms / 1000.0))

    time.sleep(wait_ms / 1000.0)

    start_ms = millis()
    started = 0
    while started < len(pairs):
        # number of expected tests to have been run
        started_expected = math.ceil(((millis() - start_ms) / test_duration_ms) * len(pairs))
        if started_expected > started:
            for _ in range(0, started_expected - started):
                (nssource, nstarget) = pairs.pop()
                nstarget_addr = get_ipv6_address(nstarget, interface)

                if args.verbosity == 'verbose':
                    print('[{:06}] Ping {} => {} ({} / {})'.format(millis() - start_ms, nssource, nstarget, nstarget_addr, interface))

                command = ['ip', 'netns', 'exec', nssource ,'ping', '-c', str(ping_count), '-w', str(ping_deadline), '-D', nstarget_addr]
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                processes.append(process)
                started += 1
        else:
            # sleep a small amount
            time.sleep(test_duration_ms / len(pairs) / 1000.0 / 10.0)

    stop1_ms = millis()

    # wait until test_duration_ms is over
    if (stop1_ms - start_ms) < test_duration_ms:
        time.sleep((test_duration_ms - (stop1_ms - start_ms)) / 1000.0)

    stop2_ms = millis()

    ts_end = get_traffic_statistics(nsnames)

    result_packets_send = 0
    result_packets_received = 0
    result_rtt_avg = 0.0

    # wait/collect for results from pings (prolongs testing up to 1 second!)
    for process in processes:
        process.wait()
        (output, err) = process.communicate()
        result = parse_ping(output.decode())

        result_packets_send += ping_count
        result_packets_received += result.received
        result_rtt_avg += result.rtt_avg

    result_rtt_avg = 0.0 if result_packets_received == 0 else (result_rtt_avg / result_packets_received)
    result_duration_ms = stop1_ms - start_ms
    result_filler_ms = stop2_ms - stop1_ms
    result_ingress_avg_node_kbs = 0.0 if (len(nsnames) == 0) else (1000.0 * (ts_end.rx_bytes - ts_beg.rx_bytes) / (stop2_ms - start_ms) / len(nsnames))
    result_egress_avg_node_kbs = 0.0 if (len(nsnames) == 0) else (1000.0 * (ts_end.tx_bytes - ts_beg.tx_bytes) / (stop2_ms - start_ms) / len(nsnames))
    result_lost = 0 if (result_packets_send == 0) else (100.0 - 100.0 * (result_packets_received / result_packets_send))
    lavg = get_load_average()

    if outfile is not None:
        header = (
            'load_avg1 load_avg5 load_avg15 '
            'node_count '
            'packets_send '
            'packets_received '
            'sample_duration_ms '
            'rtt_avg_ms '
            'egress_avg_node_kbs '
            'ingress_avg_node_kbs\n'
        )

        # add csv header if not present
        add_csv_header(outfile, header.replace(' ', args.csv_delimiter))

        outfile.write('{:0.2f} {:0.2f} {:0.2f} {} {} {} {} {} {:0.2f}\n'.format(
            lavg[0], lavg[1], lavg[2],
            len(nsnames),
            result_packets_send,
            result_packets_received,
            int(result_duration_ms + result_filler_ms),
            int(result_rtt_avg),
            result_egress_avg_node_kbs,
            result_ingress_avg_node_kbs
        ).replace(' ', args.csv_delimiter))

    if args.verbosity != 'quiet':
        print('send: {}, received: {}, load: {}/{}/{}, lost: {:0.2f}%, measurement span: {}ms + {}ms, egress: {}/s/node, ingress: {}/s/node'.format(
            result_packets_send,
            result_packets_received,
            lavg[0], lavg[1], lavg[2],
            result_lost,
            result_duration_ms,
            result_filler_ms,
            format_bytes(result_egress_avg_node_kbs),
            format_bytes(result_ingress_avg_node_kbs)
        ))

class TrafficStatisticSummary:
    def __init__(self):
        self.rx_bytes = 0
        self.rx_packets = 0
        self.tx_bytes = 0
        self.tx_packets = 0

    def print(self):
        print('received {} ({} bytes, {} packets), send: {} ({} bytes, {} packets)'.format(
            format_bytes(self.rx_bytes), self.rx_bytes, self.rx_packets,
            format_bytes(self.tx_bytes), self.tx_bytes, self.tx_packets
        ))

def format_bytes(size):
    power = 1000
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T', 5: 'E'}
    while size > power:
        size /= power
        n += 1
    return '{:.2f} {}B'.format(size, power_labels[n])

def get_traffic_statistics(nsnames):
    # fetch uplink statistics
    ret = TrafficStatisticSummary()

    for nsname in nsnames:
        command = ['ip', 'netns', 'exec', nsname , 'ip', '-statistics', 'link', 'show', 'dev', 'uplink']
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (output, err) = process.communicate()
        lines = output.decode().split('\n')
        link_toks = lines[1].split()
        rx_toks = lines[3].split()
        tx_toks = lines[5].split()
        ret.rx_bytes += int(rx_toks[0])
        ret.rx_packets += int(rx_toks[1])
        ret.tx_bytes += int(tx_toks[0])
        ret.tx_packets += int(tx_toks[1])

    return ret

# Set some IPv6 address
def setup_uplink(nsname, interface):
    def eui64_suffix(nsname, interface):
        mac = get_mac_address(nsname, interface)
        return '{:02x}{}:{}ff:fe{}:{}{}'.format(
            int(mac[0:2], 16) ^ 2, # byte with flipped bit
            mac[3:5], mac[6:8], mac[9:11], mac[12:14], mac[15:17]
        )

    exec('ip netns exec "{}" ip link set "{}" down'.format(nsname, interface))
    exec('ip netns exec "{}" ip link set "{}" up'.format(nsname, interface))
    exec('ip netns exec "{}" ip address add fdef:17a0:ffb1:300:{}/64 dev {}'.format(
        nsname,
        eui64_suffix(nsname, interface),
        interface
    ))

def pkill(pname):
    for _ in range(0, 10):
        rc = os.system('pkill -9 {}'.format(pname))
        if rc != 0:
            # no process found to kill
            return
        time.sleep(1)

    eprint('Failed to kill {}'.format(pname))
    exit(1)

def start_none_instances(nsnames):
    # nothing to do
    pass

def stop_none_instances(nsnames):
    # nothing to do
    pass

def start_yggdrasil_instances(nsnames):
    for nsname in nsnames:
        if args.verbosity == 'verbose':
            print('start yggdrasil on {}'.format(nsname))

        # Create a configuration file
        configfile = '/tmp/yggdrasil-{}.conf'.format(nsname)
        f = open(configfile, 'w')
        f.write('AdminListen: none')
        f.close()

        exec('ip netns exec "{}" yggdrasil -useconffile {}'.format(nsname, configfile), True)

def stop_yggdrasil_instances(nsnames):
    exec('rm -f /tmp/yggdrasil-*.conf')

    if args.verbosity == 'verbose':
       print('stop yggdrasil in all namespaces')

    if len(nsnames) > 0:
        pkill('yggdrasil')

def start_batmanadv_instances(nsnames):
    for nsname in nsnames:
        if args.verbosity == 'verbose':
            print('start batman-adv on {}'.format(nsname))

        exec('ip netns exec "{}" ip link set "{}" down'.format(nsname, 'uplink'))
        exec('ip netns exec "{}" ip link set "{}" up'.format(nsname, 'uplink'))
        exec('ip netns exec "{}" batctl meshif "bat0" interface add "uplink"'.format(nsname))
        setup_uplink(nsname, 'bat0')

def stop_batmanadv_instances(nsnames):
    for nsname in nsnames:
        if args.verbosity == 'verbose':
            print('stop batman-adv on {}'.format(nsname))

        exec('ip netns exec "{}" batctl meshif "bat0" interface del "uplink"'.format(nsname))

def start_babel_instances(nsnames):
    for nsname in nsnames:
        if args.verbosity == 'verbose':
            print('start babel on {}'.format(nsname))

        setup_uplink(nsname, 'uplink')
        exec('ip netns exec "{}" babeld -D -I /tmp/babel-{}.pid "uplink"'.format(nsname, nsname))

def stop_babel_instances(nsnames):
    if args.verbosity == 'verbose':
        print('stop babel in all namespaces')

    if len(nsnames) > 0:
        pkill('babeld')
        exec('rm -f /tmp/babel-*.pid')

def start_olsr2_instances(nsnames):
    for nsname in nsnames:
        if args.verbosity == 'verbose':
            print('start olsr2 on {}'.format(nsname))

        # Create a configuration file
        # Print all settings: olsrd2_static --schema=all
        configfile = '/tmp/olsrd2-{}.conf'.format(nsname)
        f = open(configfile, 'w')
        f.write(
            '[global]\n'
            'fork       yes\n'
            'lockfile   -\n'
            '\n'
            # restrict to IPv6
            '[olsrv2]\n'
            'originator  -0.0.0.0/0\n'
            'originator  -::1/128\n'
            'originator  default_accept\n'
            '\n'
            # restrict to IPv6
            '[interface]\n'
            'bindto  -0.0.0.0/0\n'
            'bindto  -::1/128\n'
            'bindto  default_accept\n'
            )
        f.close()

        setup_uplink(nsname, 'uplink')
        exec('ip netns exec "{}" olsrd2 "uplink" --load {}'.format(nsname, configfile))

def stop_olsr2_instances(nsnames):
    if args.verbosity == 'verbose':
        print('stop olsr2 in all namespaces')

    if len(nsnames) > 0:
        pkill('olsrd2')
        exec('rm -f /tmp/olsrd2-*.conf')

def start_bmx7_instances(nsnames):
    for nsname in nsnames:
        if args.verbosity == 'verbose':
            print('start bmx7 on {}'.format(nsname))

        exec('rm -rf /tmp/bmx7_*')
        setup_uplink(nsname, 'uplink')
        exec('ip netns exec "{}" bmx7 --runtimeDir /tmp/bmx7_{} dev=uplink'.format(nsname, nsname))

def stop_bmx7_instances(nsnames):
    if args.verbosity == 'verbose':
        print('stop bmx7 in all namespaces')

    if len(nsnames) > 0:
        pkill('bmx7')
        exec('rm -rf /tmp/bmx7_*')

def start_bmx6_instances(nsnames):
    for nsname in nsnames:
        if args.verbosity == 'verbose':
            print('start bmx6 on {}'.format(nsname))

        exec('rm -rf /tmp/bmx6_*')
        setup_uplink(nsname, 'uplink')
        exec('ip netns exec "{}" bmx6 --runtimeDir /tmp/bmx6_{} dev=uplink'.format(nsname, nsname, nsname))

def stop_bmx6_instances(nsnames):
    if args.verbosity == 'verbose':
        print('stop bmx6 in all namespaces')

    if len(nsnames) > 0:
        pkill('bmx6')
        exec('rm -rf /tmp/bmx6_*')

def start_routing_protocol(protocol, nsnames):
    if protocol == 'batman-adv':
        start_batmanadv_instances(nsnames)
    elif protocol == 'yggdrasil':
        start_yggdrasil_instances(nsnames)
    elif protocol == 'babel':
        start_babel_instances(nsnames)
    elif protocol == 'olsr2':
        start_olsr2_instances(nsnames)
    elif protocol == 'bmx6':
        start_bmx6_instances(nsnames)
    elif protocol == 'bmx7':
        start_bmx7_instances(nsnames)
    elif protocol == 'none':
        start_none_instances(nsnames)
    else:
        eprint('Error: unknown routing protocol: {}'.format(protocol))
        exit(1)

def stop_routing_protocol(protocol, nsnames):
    if protocol == 'batman-adv':
        stop_batmanadv_instances(nsnames)
    elif protocol == 'yggdrasil':
        stop_yggdrasil_instances(nsnames)
    elif protocol == 'babel':
        stop_babel_instances(nsnames)
    elif protocol == 'olsr2':
        stop_olsr2_instances(nsnames)
    elif protocol == 'bmx6':
        stop_bmx6_instances(nsnames)
    elif protocol == 'bmx7':
        stop_bmx7_instances(nsnames)
    elif protocol == 'none':
        stop_none_instances(nsnames)
    else:
        eprint('Error: unknown routing protocol: {}'.format(protocol))
        exit(1)

parser = argparse.ArgumentParser()
parser.add_argument('protocol',
    choices=['none', 'babel', 'batman-adv', 'olsr2', 'bmx6', 'bmx7', 'yggdrasil'],
    help='Routing protocol to set up.')
parser.add_argument('--verbosity',
    choices=['verbose', 'normal', 'quiet'],
    default='normal',
    help='Set verbosity.')
parser.add_argument('--seed',
    type=int,
    help='Seed the random generator.')
parser.add_argument('--csv-out',
    help='Write CSV formatted data to file.')
parser.add_argument('--csv-delimiter',
    default='\t'
    help='Delimiter for CSV output columns. Default: tab character')

subparsers = parser.add_subparsers(dest='action', required=True, help='Action')
parser_start = subparsers.add_parser('start', help='Start protocol daemons in every namespace.')
parser_stop = subparsers.add_parser('stop', help='Stop protocol daemons in every namespace.')
parser_test = subparsers.add_parser('test', help='Measure reachability and traffic.')
parser_test.add_argument('--duration', type=int, default=1, help='Duration in seconds for this test.')
parser_test.add_argument('--samples', type=int, default=10, help='Number of random paths to test.')
parser_test.add_argument('--wait', type=int, default=0, help='Seconds to wait after the begin of the traffic measurement before pings are send.')

args = parser.parse_args()

if os.popen('id -u').read().strip() != '0':
    sys.stderr.write('Need to run as root.\n')
    exit(1)

random.seed(args.seed)

# all ns-* network namespaces
nsnames = [x for x in os.popen('ip netns list').read().split() if x.startswith('ns-')]

# network interface to send packets to/from
uplink_interface = 'uplink'

outfile = None
if args.csv_out is not None:
    outfile = open(args.csv_out, 'a+')

# batman-adv uses its own interface as entry point to the mesh
if args.protocol == 'batman-adv':
    uplink_interface = 'bat0'
elif args.protocol == 'yggdrasil':
    uplink_interface = 'tun0'


if args.action == 'start':
    start_routing_protocol(args.protocol, nsnames)
elif args.action == 'stop':
    stop_routing_protocol(args.protocol, nsnames)
elif args.action == 'test':
    run_test(nsnames, uplink_interface, args.samples, args.duration * 1000, args.wait * 1000.0, outfile)
else:
    sys.stderr.write('Unknown action: {}\n'.format(args.action))
    exit(1)
