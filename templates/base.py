#
# Author:: Noah Kantrowitz <noah@coderanger.net>
#
# Copyright 2014, Balanced, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import troposphere.elasticloadbalancing

import stratosphere
from stratosphere import And, Equals, Not, NoValue, If, GetAtt, Ref, Join, Base64


class ConditionalAZMixin(object):
    """A mixing to load some default parameters for multi-AZ objects."""

    CONDITIONAL_AZ_ATTRS = ['cond', 'subnet', 'public_subnet', 'gateway_security_group']
    AZS = ['a', 'b', 'c']

    def __init__(self, *args, **kwargs):
        template = kwargs.get('template')
        for attr in self.CONDITIONAL_AZ_ATTRS:
            for az in self.AZS:
                camel = ''.join(s.capitalize() for s in attr.split('_')) + az.upper()
                value = None
                if camel in kwargs:
                    value = kwargs.pop(camel)
                elif template:
                    template_attr = 'param_{}'.format(camel)
                    if attr == 'cond':
                        template_attr = 'cond_Has{}'.format(az.upper())
                    if hasattr(template, template_attr):
                        if attr == 'cond':
                            value = 'Has{}'.format(az.upper())
                        else:
                            value = Ref(getattr(template, template_attr)())
                setattr(self, '_{}_{}'.format(attr, az), value)
        super(ConditionalAZMixin, self).__init__(*args, **kwargs)


class SecurityGroup(ConditionalAZMixin, stratosphere.ec2.SecurityGroup):
    def __init__(self, *args, **kwargs):
        self._allow = kwargs.pop('Allow', [])
        self._allow_self = kwargs.pop('AllowSelf', True)
        self._allow_ssh = kwargs.pop('AllowSSH', False)
        self._gateway_ssh = kwargs.pop('GatewaySSH', True)
        super(SecurityGroup, self).__init__(*args, **kwargs)

    def VpcId(self):
        return Ref(self.template.param_VpcId())

    def SecurityGroupIngress(self):
        rules = []
        rules.append(stratosphere.ec2.SecurityGroupRule(
            'ICMP',
            IpProtocol='icmp',
            FromPort='-1',
            ToPort='-1',
            CidrIp='0.0.0.0/0',
        ))
        if self._allow_ssh:
            rules.append(stratosphere.ec2.SecurityGroupRule(
                'SSH',
                IpProtocol='tcp',
                FromPort='22',
                ToPort='22',
                CidrIp='0.0.0.0/0',
            ))
        elif self._gateway_ssh:
            if self._cond_a:
                rules.append(If(
                    self._cond_a,
                    stratosphere.ec2.SecurityGroupRule(
                        'SSHA',
                        IpProtocol='tcp',
                        FromPort='22',
                        ToPort='22',
                        SourceSecurityGroupId=self._gateway_security_group_a,
                    ),
                    NoValue
                ))
            if self._cond_b:
                rules.append(If(
                    self._cond_b,
                    stratosphere.ec2.SecurityGroupRule(
                        'SSHB',
                        IpProtocol='tcp',
                        FromPort='22',
                        ToPort='22',
                        SourceSecurityGroupId=self._gateway_security_group_b,
                    ),
                    NoValue
                ))
            if self._cond_c:
                rules.append(If(
                    self._cond_c,
                    stratosphere.ec2.SecurityGroupRule(
                        'SSHC',
                        IpProtocol='tcp',
                        FromPort='22',
                        ToPort='22',
                        SourceSecurityGroupId=self._gateway_security_group_c,
                    ),
                    NoValue
                ))
        for port in self._allow:
            rules.append(stratosphere.ec2.SecurityGroupRule(
                'Port{0}'.format(port),
                IpProtocol='tcp',
                FromPort=str(port),
                ToPort=str(port),
                CidrIp='0.0.0.0/0',
            ))
        return rules

    def post_add(self, template):
        if self._allow_self:
            template.add_resource(stratosphere.ec2.SecurityGroupIngress(
                self.name + 'SelfTCPIngress',
                IpProtocol='tcp',
                FromPort='0',
                ToPort='65535',
                GroupId=Ref(self),
                SourceSecurityGroupId=Ref(self),
            ))
            template.add_resource(stratosphere.ec2.SecurityGroupIngress(
                self.name + 'SelfUDPIngress',
                IpProtocol='udp',
                FromPort='0',
                ToPort='65535',
                GroupId=Ref(self),
                SourceSecurityGroupId=Ref(self),
            ))
            template.add_resource(stratosphere.ec2.SecurityGroupIngress(
                self.name + 'SelfICMPIngress',
                IpProtocol='icmp',
                FromPort='-1',
                ToPort='-1',
                GroupId=Ref(self),
                SourceSecurityGroupId=Ref(self),
            ))


class LoadBalancer(ConditionalAZMixin, stratosphere.elasticloadbalancing.LoadBalancer):
    def __init__(self, *args, **kwargs):
        self._scheme = kwargs.pop('Scheme', 'internal')
        self._port = kwargs.pop('Port', '80')
        self._ssl_certificate_id = kwargs.pop('SSLCertificateId', None)
        self._security_group = kwargs.pop('SecurityGroup', None)
        self._health_url = kwargs.pop('HealthUrl', '/health')
        super(LoadBalancer, self).__init__(*args, **kwargs)

    def Scheme(self):
        return self._scheme

    def SecurityGroups(self):
        if self._security_group:
            return [self._security_group]

    def Listeners(self):
        listeners = [self._http_listener()]
        if self._ssl_certificate_id:
            listeners.append(self._https_listener())
        return listeners

    def _http_listener(self):
        return troposphere.elasticloadbalancing.Listener(
            LoadBalancerPort='80',
            InstancePort=self._port,
            Protocol='HTTP',
            InstanceProtocol='HTTP',
        )

    def _https_listener(self):
        return troposphere.elasticloadbalancing.Listener(
            LoadBalancerPort='443',
            InstancePort=self._port,
            Protocol='HTTPS',
            InstanceProtocol='HTTP',
            SSLCertificateId=Join('', [
                'arn:aws:iam::',
                Ref('AWS::AccountId'),
                ':server-certificate/',
                self._ssl_certificate_id,
            ]),
        )

    def HealthCheck(self):
        if self._health_url:
            return troposphere.elasticloadbalancing.HealthCheck(
                Target=Join('', ['HTTP:', self._port, self._health_url]),
                HealthyThreshold='3',
                UnhealthyThreshold='5',
                Interval='30',
                Timeout='5',
            )

    def Subnets(self):
        subnets = []
        possible_subnets = [
            (self._cond_a, self._subnet_a, self._public_subnet_a),
            (self._cond_b, self._subnet_b, self._public_subnet_b),
            (self._cond_c, self._subnet_c, self._public_subnet_c),
        ]
        for cond, subnet, public_subnet in possible_subnets:
            if cond:
                if self._scheme != 'internal':
                    subnet = public_subnet
                subnets.append(If(cond, subnet, NoValue))
        return subnets


class LaunchConfiguration(stratosphere.autoscaling.LaunchConfiguration):
    def __init__(self, *args, **kwargs):
        self._security_group = kwargs.pop('SecurityGroup', None)
        self._chef_recipe = kwargs.pop('ChefRecipe')
        self._chef_env = kwargs.pop('ChefEnv')
        self._name_tag = kwargs.pop('NameTag', 'ec2')
        super(LaunchConfiguration, self).__init__(*args, **kwargs)

    def IamInstanceProfile(self):
        return Ref(self.template.insp())

    def ImageId(self):
        return Ref(self.template.param_AmiId())

    def KeyName(self):
        return Ref(self.template.param_KeyName())

    def SecurityGroups(self):
        if self._security_group:
            return [self._security_group]

    def UserData(self):
        return Base64(Join('', [
            '#!/bin/bash -xe\n',
            '/opt/bootstrap.sh "', self._name_tag, '" "', self._chef_env, '" "',  self._chef_recipe, '"\n',
        ]))


class AutoScalingGroup(ConditionalAZMixin, stratosphere.autoscaling.AutoScalingGroup):
    def AvailabilityZones(self):
        zones = []
        if self._cond_a:
            zones.append(If(self._cond_a, Join('', [Ref('AWS::Region'), 'a']), NoValue))
        if self._cond_b:
            zones.append(If(self._cond_b, Join('', [Ref('AWS::Region'), 'b']), NoValue))
        if self._cond_c:
            zones.append(If(self._cond_c, Join('', [Ref('AWS::Region'), 'c']), NoValue))
        return zones

    def LaunchConfigurationName(self):
        return Ref(self.template.lc())

    def LoadBalancerNames(self):
        return [Ref(self.template.elb())]

    def MaxSize(self):
        return '1'

    def MinSize(self):
        return '1'

    def VPCZoneIdentifier(self):
        subnets = []
        if self._cond_a:
            subnets.append(If(self._cond_a, self._subnet_a, NoValue))
        if self._cond_b:
            subnets.append(If(self._cond_b, self._subnet_b, NoValue))
        if self._cond_c:
            subnets.append(If(self._cond_c, self._subnet_c, NoValue))
        return subnets


class Stack(stratosphere.cloudformation.Stack):
    # Find a better way to do this
    TEMPLATES = {}

    def __init__(self, *args, **kwargs):
        self._parameters = kwargs.pop('Parameters', {})
        self._template_name = kwargs.pop('TemplateName', None)
        super(Stack, self).__init__(*args, **kwargs)

    def TemplateURL(self):
        if self._template_name:
            if 'sha1' not in self.TEMPLATES.get(self._template_name, {}):
                raise ValueError('Unknown template {}'.format(self._template_name))
            return Join('', [
                'https://balanced-cfn-',
                Ref('AWS::Region'),
                '.s3.amazonaws.com/templates/{}-{}.json'.format(self._template_name, self.TEMPLATES[self._template_name]['sha1']),
            ])

    def Parameters(self):
        # Default stack parameters
        params = {
            'VpcId': Ref(self.template.param_VpcId() or self.template.vpc()),
            'KeyName': Ref(self.template.param_KeyName()),
        }
        params.update(self._parameters)
        return params


class Template(stratosphere.Template):
    """Defaults and mixins for Balanced templates."""

    @classmethod
    def STRATOSPHERE_TYPES(cls):
        types = stratosphere.Template.STRATOSPHERE_TYPES()
        types.update({
            'asg': AutoScalingGroup,
            'elb': LoadBalancer,
            'lc': LaunchConfiguration,
            'sg': SecurityGroup,
            'stack': Stack,
        })
        return types

    def param_VpcId(self):
        """VPC ID."""
        return {'Type': 'String'}

    def param_KeyName(self):
        """SSH key name."""
        return {'Type': 'String', 'Default': 'cloudformation'}


class RoleMixin(stratosphere.Template):
    CITADEL_FOLDERS = []
    S3_BUCKETS = []
    IAM_STATEMENTS = []

    def role(self):
        """IAM role for Balanced docs."""
        citadel_folders = ['newrelic', 'deploy_key'] + self.CITADEL_FOLDERS
        s3_buckets = ['balanced-citadel/{}'.format(s) for s in citadel_folders] + ['balanced.debs', 'apt.vandelay.io'] + self.S3_BUCKETS
        s3_objects = ['arn:aws:s3:::{}/*'.format(s) for s in s3_buckets]
        return {
            'Statements': [
                {
                    'Effect': 'Allow',
                    'Action': 's3:GetObject',
                    'Resource': s3_objects,
                },
                {
                    'Effect': 'Allow',
                    'Action': [
                        'route53:GetHostedZone',
                        'route53:ListResourceRecordSets',
                        'route53:ChangeResourceRecordSets',
                  ],
                  'Resource': 'arn:aws:route53:::hostedzone/Z2IP8RX9IARH86',
                },
            ] + self.IAM_STATEMENTS,
        }

    def insp(self):
        """IAM instance profile."""
        return {
            'Description': 'IAM instance profile for {}'.format(self.__class__.__name__),
            'Roles': [Ref(self.role())],
        }


class AppTemplate(RoleMixin, Template):
    """A model for Cloud Formation stack for a Balanced application."""

    # Parameter defaults
    ENV = 'production'
    CHEF_RECIPE = None
    STACK_TAG = None
    INSTANCE_TYPE = 'm1.small'
    CAPACITY = 1
    PUBLIC = False
    PORT = 80

    def param_ChefRecipe(self):
        """Chef recipe name."""
        if not self.CHEF_RECIPE:
            raise ValueError('CHEF_RECIPE not set for {}'.format(self.__class__.__name__))
        return {'Type': 'String', 'Default': self.CHEF_RECIPE}

    def param_Tag(self):
        """Stack tag."""
        if not self.STACK_TAG:
            raise ValueError('STACK_TAG not set for {}'.format(self.__class__.__name__))
        return {'Type': 'String', 'Default': self.STACK_TAG}

    def param_Env(self):
        """Logical environment."""
        return {'Type': 'String', 'AllowedValues': ['production', 'test', 'misc'], 'Default': 'production'}

    def param_ChefEnv(self):
        """Configuration environment."""
        return {'Type': 'String', 'Default': self.ENV}

    def param_InstanceType(self):
        """Instance type."""
        return {'Type': 'String', 'Default': self.INSTANCE_TYPE}

    def param_Capacity(self):
        """Instance count."""
        return {'Type': 'Number', 'Default': str(self.CAPACITY)}

    def param_AmiId(self):
        """Amazon machine image."""
        return {'Type': 'String'}

    def param_SubnetA(self):
        """Subnet ID for AZ A. Optional."""
        return {'Type': 'String', 'Default': ''}

    def param_SubnetB(self):
        """Subnet ID for AZ B. Optional."""
        return {'Type': 'String', 'Default': ''}

    def param_SubnetC(self):
        """Subnet ID for AZ C. Optional."""
        return {'Type': 'String', 'Default': ''}

    def param_PublicSubnetA(self):
        """Public subnet ID for AZ A. Optional."""
        return {'Type': 'String', 'Default': ''}

    def param_PublicSubnetB(self):
        """Public subnet ID for AZ B. Optional."""
        return {'Type': 'String', 'Default': ''}

    def param_PublicSubnetC(self):
        """Public subnet ID for AZ C. Optional."""
        return {'Type': 'String', 'Default': ''}

    def param_GatewaySecurityGroupA(self):
        """Security group ID for AZ A Gateway instances. Optional."""
        return {'Type': 'String', 'Default': ''}

    def param_GatewaySecurityGroupB(self):
        """Security group ID for AZ B Gateway instances. Optional."""
        return {'Type': 'String', 'Default': ''}

    def param_GatewaySecurityGroupC(self):
        """Security group ID for AZ C Gateway instances. Optional."""
        return {'Type': 'String', 'Default': ''}

    def cond_HasA(self):
        """Condition checking if AZ A is usable."""
        return And(
            Not(Equals(Ref(self.param_SubnetA()), '')),
            Not(Equals(Ref(self.param_GatewaySecurityGroupA()), '')),
        )

    def cond_HasB(self):
        """Condition checking if AZ B is usable."""
        return And(
            Not(Equals(Ref(self.param_SubnetB()), '')),
            Not(Equals(Ref(self.param_GatewaySecurityGroupB()), '')),
        )

    def cond_HasC(self):
        """Condition checking if AZ C is usable."""
        return And(
            Not(Equals(Ref(self.param_SubnetC()), '')),
            Not(Equals(Ref(self.param_GatewaySecurityGroupC()), '')),
        )

    def out_ELBHostname(self):
        """Return the hostname of the ELB for later use."""
        return {'Value': GetAtt(self.elb(), 'DNSName')}

    def sg(self):
        """Security group."""
        return {
            'Description': 'Security group for {}'.format(self.__class__.__name__),
            'Allow': [self.PORT],
        }

    def sg_LoadBalancerSecurityGroup(self):
        """Load balanacer security group."""
        ports = [80]
        if self.PUBLIC:
            ports.append(443)
        return {
            'Description': 'Security group for {} load balancer'.format(self.__class__.__name__),
            'Allow': ports,
            'GatewaySSH': False,
            'AllowSelf': False,
        }

    def elb(self):
        """Load balancer."""
        return {
            'Description': 'Load balancer for {}'.format(self.__class__.__name__),
            'Scheme': None if self.PUBLIC else 'internal',
            'HealthUrl': '/health',
            'Port': self.PORT,
            'SecurityGroup': Ref(self.sg_LoadBalancerSecurityGroup()),
        }

    def lc(self):
        """ASG launch configuration."""
        return {
            'Description': 'ASG launch configuration for {}'.format(self.__class__.__name__),
            'SecurityGroup': Ref(self.sg()),
            'ChefRecipe': Ref(self.param_ChefRecipe()),
            'ChefEnv': Ref(self.param_ChefEnv()),
            'NameTag': Ref(self.param_Tag()),
            'InstanceType': Ref(self.param_InstanceType()),
        }

    def asg(self):
        """Autoscaling group."""
        return {
            'Description': 'Autoscaling group for {}'.format(self.__class__.__name__),
            'MinSize': Ref(self.param_Capacity()),
            'MaxSize': Ref(self.param_Capacity()),
        }
