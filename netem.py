#!/usr/bin/env python3

import logging
import argparse
import subprocess

import yaml


LOGGER = logging.getLogger()


def run_cmd(cmd, check=True):
    LOGGER.debug('Running command: %s', ' '.join(cmd))
    subprocess.run(cmd, check=check)


def clear_all(netif):
    run_cmd(['tc', 'qdisc', 'del', 'dev', netif, 'root'], check=False)
    run_cmd(['tc', 'qdisc', 'del', 'dev', netif, 'ingress'], check=False)
    run_cmd(['modprobe', '-r', 'ifb'], check=False)


class Interface:
    def __init__(self, netif, rate):
        self.parent_classid = '1:1'
        self.classids = []
        self.netif = netif
        self.rate = '%sMbit' % rate
        self._created = False

    def _create(self):
        if self._created:
            return
        run_cmd(['tc', 'qdisc', 'add', 'dev', self.netif, 'root', 'handle', '1:', 'htb'])
        run_cmd([
            'tc', 'class', 'add', 'dev', self.netif, 'parent', '1:', 'classid',
            self.parent_classid, 'htb', 'rate', self.rate
            ])
        self._created = True

    def _add_netem(self, netem_args, cidrs, cidrtype):
        self._create()
        classid = '1:%s' % str(len(self.classids) + 10)
        self.classids.append(classid)

        run_cmd([
            'tc', 'class', 'add', 'dev', self.netif, 'parent', self.parent_classid,
            'classid', classid, 'htb', 'rate', self.rate
            ])
        netem_cmd = [
            'tc', 'qdisc', 'add', 'dev', self.netif, 'parent', classid, 'netem'
            ]
        netem_cmd.extend(netem_args)
        run_cmd(netem_cmd)

        for cidr in cidrs:
            run_cmd([
                'tc', 'filter', 'add', 'dev', self.netif, 'protocol', 'ip', 'parent', '1:', 'u32',
                'match', 'ip', cidrtype, cidr, 'flowid', classid
                ])


class OutInterface(Interface):
    def __init__(self, netif, rate=1000):
        super().__init__(netif, rate)

    def add_netem(self, netem_args, cidrs):
        self._add_netem(netem_args, cidrs, cidrtype='dst')


class InInterface(Interface):
    def __init__(self, netif, rate=1000, ifbif='ifb0'):
        self._create_ifb(netif, ifbif)
        super().__init__(ifbif, rate)

    @staticmethod
    def _create_ifb(netif, ifbif):
        run_cmd(['modprobe', 'ifb'])
        run_cmd(['ip', 'link', 'set', 'dev', ifbif, 'up'])
        run_cmd(['tc', 'qdisc', 'add', 'dev', netif, 'handle', 'ffff:', 'ingress'])
        run_cmd([
            'tc', 'filter', 'add', 'dev', netif, 'parent', 'ffff:', 'protocol', 'all', 'u32',
            'match', 'u32', '0', '0', 'action', 'mirred', 'egress', 'redirect', 'dev', ifbif
            ])

    def add_netem(self, netem_args, cidrs):
        self._add_netem(netem_args, cidrs, cidrtype='src')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-l', '--log-level',
        choices=[
            'critical',
            'error',
            'warning',
            'info',
            'debug'
        ],
        default='info',
        help='Set log level for console output'
    )
    parser.add_argument('-c', '--clear', action='store_true', help='Clear config')
    parser.add_argument('-f', '--config', metavar='FILENAME', default='config.yaml',
                        help='Path to config file')
    args = parser.parse_args()

    # Set up logging
    LOGGER.setLevel(args.log_level.upper())
    console = logging.StreamHandler()
    LOGGER.addHandler(console)

    with open(args.config, 'r') as f:
        LOGGER.debug('Loading config...')
        config = yaml.load(f)
        LOGGER.debug(yaml.dump(config, default_flow_style=False))

    for netif, params in config.items():
        clear_all(netif)
        if args.clear:
            continue

        in_netem = params.get('in', [])
        out_netem = params.get('out', [])
        in_netem.extend(params.get('both', []))
        out_netem.extend(params.get('both', []))

        if in_netem:
            in_if = InInterface(netif)
            for i in in_netem:
                LOGGER.info("Adding incoming '%s' for '%s' on interface '%s'", i['netem'],
                             ', '.join(i['cidrs']), netif)
                in_if.add_netem(i['netem'].split(), i['cidrs'])

        if out_netem:
            out_if = OutInterface(netif)
            for i in out_netem:
                LOGGER.info("Adding outgoing '%s' for '%s' on interface '%s'", i['netem'],
                             ', '.join(i['cidrs']), netif)
                out_if.add_netem(i['netem'].split(), i['cidrs'])


if __name__ == '__main__':
    main()
