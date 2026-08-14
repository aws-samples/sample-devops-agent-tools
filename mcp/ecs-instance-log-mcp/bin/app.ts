#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { EcsLogGatewayStackV2 } from '../src/ecs-log-gateway-stack-v2';

const app = new cdk.App();
const stackName = process.env.CDK_STACK_NAME ?? 'EcsInstanceLogMcpStack';

const parseEnvironmentList = (name: string): string[] | undefined => {
  const values = Array.from(new Set(
    (process.env[name] ?? '').split(',').map(value => value.trim()).filter(Boolean),
  ));
  return values.length > 0 ? values : undefined;
};

new EcsLogGatewayStackV2(app, stackName, {
  description: 'ECS Instance Log MCP Server - Production-grade log collection for DevOps Agent with byte-range streaming and incident analysis',
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  gatewayProps: {
    gatewayName: 'EcsInstanceLogMcpGW',
    logRetentionDays: 1,
    enableKmsEncryption: process.env.ENABLE_KMS_ENCRYPTION
      ? !['0', 'false', 'no'].includes(process.env.ENABLE_KMS_ENCRYPTION.toLowerCase())
      : true,
    ssmDefaultHostRoleArn: process.env.SSM_DEFAULT_HOST_ROLE_ARN?.trim() || undefined,
    allowedClusterNames: parseEnvironmentList('ALLOWED_CLUSTER_NAMES'),
    allowedRegions: parseEnvironmentList('ALLOWED_REGIONS'),
    ecsInstanceRoleArns: parseEnvironmentList('ECS_INSTANCE_ROLE_ARNS'),
    requireCollectionApproval: process.env.REQUIRE_COLLECTION_APPROVAL
      ? !['0', 'false', 'no'].includes(process.env.REQUIRE_COLLECTION_APPROVAL.toLowerCase())
      : undefined,
    approvalApproverArns: process.env.APPROVAL_APPROVER_ARNS
      ? process.env.APPROVAL_APPROVER_ARNS.split(',').map(value => value.trim()).filter(Boolean)
      : undefined,
    approvalNotificationEmails: process.env.APPROVAL_NOTIFICATION_EMAILS
      ? process.env.APPROVAL_NOTIFICATION_EMAILS.split(',').map(value => value.trim()).filter(Boolean)
      : undefined,
    approvalTtlSeconds: process.env.APPROVAL_TTL_SECONDS
      ? parseInt(process.env.APPROVAL_TTL_SECONDS, 10)
      : undefined,
    enableRestrictedTools: process.env.ENABLED_RESTRICTED_TOOLS
      ? process.env.ENABLED_RESTRICTED_TOOLS.split(',').map(value => value.trim()).filter(Boolean)
      : undefined,
    pcapPresignedUrlExpirationSeconds: process.env.PCAP_PRESIGNED_URL_EXPIRATION
      ? parseInt(process.env.PCAP_PRESIGNED_URL_EXPIRATION, 10)
      : undefined,
    maxPcapBytes: process.env.MAX_PCAP_BYTES
      ? parseInt(process.env.MAX_PCAP_BYTES, 10)
      : undefined,
  },
});
