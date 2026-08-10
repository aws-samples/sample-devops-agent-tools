#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { EcsLogGatewayStackV2 } from '../src/ecs-log-gateway-stack-v2';

const app = new cdk.App();

new EcsLogGatewayStackV2(app, 'EcsInstanceLogMcpStack', {
  description: 'ECS Instance Log MCP Server - Production-grade log collection for DevOps Agent with byte-range streaming and incident analysis',
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  gatewayProps: {
    gatewayName: 'EcsInstanceLogMcpGW',
    logRetentionDays: 1,
    enableKmsEncryption: true,
    ssmDefaultHostRoleArn: process.env.SSM_DEFAULT_HOST_ROLE_ARN,
    ecsInstanceRoleArns: process.env.ECS_INSTANCE_ROLE_ARNS
      ? process.env.ECS_INSTANCE_ROLE_ARNS.split(',').filter(Boolean)
      : undefined,
  },
});
