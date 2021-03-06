#!/usr/bin/env python

# Copyright 2008-2018 Univa Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import configparser
import os.path
import sys

import boto3

from tortuga.cli.tortugaCli import TortugaCli
from tortuga.config.configManager import ConfigManager
from tortuga.node.nodeApi import NodeApi
from tortuga.resourceAdapter.aws import Aws


class CancelSpotInstanceRequestsCLI(TortugaCli):
    def __init__(self):
        super(CancelSpotInstanceRequestsCLI, self).__init__(validArgCount=1)

        self.nodeApi = NodeApi()

    def parseArgs(self, usage=None):
        self.addOption('--all', action='store_true', default=False,
                       help='Cancel all spot instance requests managed by'
                            ' Tortuga')

        self.addOption('--terminate', action='store_true', default=False,
                       help='Terminate any running (fulfilled) instance(s).')

        super(CancelSpotInstanceRequestsCLI, self).parseArgs(usage=usage)

        if not self.getOptions().all and not self.getArgs():
            self.getParser().error(
                '<spot instance request id> or --all argument must be'
                ' specified')

    def runCommand(self):
        self.parseArgs(
            usage='%prog [--terminate] <spot instance request id|--all>')

        sir_instance_cache_filename = \
            os.path.join(ConfigManager().getRoot(), 'var',
                         'spot-instances.conf')

        cfg = configparser.ConfigParser()
        cfg.read(sir_instance_cache_filename)

        if self.getOptions().all:
            result = self.__get_spot_instance_request_ids(cfg)
        else:
            sir_id = self.getArgs()[0]

            result = [self.__get_spot_instance_request_id(cfg, sir_id)]

        if not result:
            # Nothing to do...
            sys.exit(0)

        self.__cancel_spot_instances(result)

    def __get_spot_instance_request_id(self, cfg, sir_id):
        # Ensure spot instance request id
        if not cfg.has_section(sir_id):
            sys.stderr.write(
                'Spot instance request [{0}] is not managed by'
                ' Tortuga\n'.format(sir_id))

            sys.exit(0)

        resource_adapter_configuration = \
            self.__get_resource_adapter_configuration(cfg, sir_id)

        return sir_id, resource_adapter_configuration

    def __get_resource_adapter_configuration(self, cfg, sir_id): \
            # pylint: disable=no-self-use
        return cfg.get(sir_id, 'resource_adapter_configuration') \
            if cfg.has_option(
                sir_id, 'resource_adapter_configuration') else \
            'default'

    def __get_spot_instance_request_ids(self, cfg):
        result = []

        for sir_id in cfg.sections():
            resource_adapter_configuration = \
                self.__get_resource_adapter_configuration(cfg, sir_id)

            result.append((sir_id, resource_adapter_configuration))

        return result

    def __get_spot_instance_request_map(self, result): \
            # pylint: disable=no-self-use
        sir_map = {}
        adapter_cfg_map = {}

        adapter = Aws()

        # Create map of spot instance requests keyed on EC2 region
        for sir_id, resource_adapter_configuration in result:
            if resource_adapter_configuration not in adapter_cfg_map:
                adapter_cfg = adapter.getResourceAdapterConfig(
                    resource_adapter_configuration)

                adapter_cfg_map[resource_adapter_configuration] = \
                    adapter_cfg
            else:
                adapter_cfg = adapter_cfg_map[
                    resource_adapter_configuration]

            if adapter_cfg['region'].name not in sir_map:
                sir_map[adapter_cfg['region'].name] = []

            sir_map[adapter_cfg['region'].name].append(sir_id)

        return sir_map

    def __cancel_spot_instances(self, result):
        sir_map = self.__get_spot_instance_request_map(result)

        aws_instance_cache = configparser.ConfigParser()
        aws_instance_cache.read('/opt/tortuga/var/aws-instance.conf')

        # Iterate on map cancelling requests in each region
        for region_name, sir_ids in sir_map.iteritems():
            session = boto3.session.Session(region_name=region_name)

            ec2_conn = session.client('ec2')

            if len(sir_ids) == 1:
                print('Cancelling spot instance request [{0}]'
                      ' in region [{1}]'.format(sir_ids[0], region_name))
            else:
                print('Cancelling {0} spot instance requests in'
                      ' region [{1}]'.format(len(sir_ids), region_name))

            response = ec2_conn.describe_spot_instance_requests(
                SpotInstanceRequestIds=sir_ids)

            # Create list of tuples (sir_id, bool) which indicate if the
            # spot instance request should be terminated
            cancelled_spot_instance_requests = []

            for sir in response['SpotInstanceRequests']:
                # All spot instance requests that are 'open' should be
                # terminated to avoid leaving orphaned Tortuga node records
                cancelled_spot_instance_requests.append(
                    (sir['SpotInstanceRequestId'],
                     self.getOptions().terminate or sir['State'] == 'open'))

            result = ec2_conn.cancel_spot_instance_requests(
                SpotInstanceRequestIds=sir_ids)

            # Delete corresponding node entries
            for sir_id, terminate in cancelled_spot_instance_requests:
                if terminate:
                    node_name = self.__get_associated_node(
                        aws_instance_cache, sir_id)
                    if node_name:
                        print('  - Deleting node [{0}]'.format(node_name))

                        self.nodeApi.deleteNode(node_name)

    def __get_associated_node(self, aws_instance_cache, sir_id): \
            # pylint: disable=no-self-use
        node_name = None

        for node_name in aws_instance_cache.sections():
            if aws_instance_cache.has_option(
                    node_name, 'spot_instance_request') and \
                aws_instance_cache.get(
                    node_name, 'spot_instance_request') == sir_id:
                break
        else:
            return None

        return node_name


def main():
    CancelSpotInstanceRequestsCLI().run()
