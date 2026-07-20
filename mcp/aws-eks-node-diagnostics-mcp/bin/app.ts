#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { EksNodeLogMcpStack } from '../src/ssm-automation-gateway-stack-v2';

const app = new cdk.App();

new EksNodeLogMcpStack(app, 'EksNodeLogMcpStack', {
  description: 'EKS Node Log MCP Server - Collect and analyze diagnostic logs from EKS worker nodes',
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  gatewayName: 'EksNodeLogMcpGW',
  enableEncryption: true,
  logRetentionDays: 1,
  ssmDefaultHostRoleArn: process.env.SSM_DEFAULT_HOST_ROLE_ARN,
  eksNodeRoleArns: process.env.EKS_NODE_ROLE_ARNS
    ? process.env.EKS_NODE_ROLE_ARNS.split(',').filter(Boolean)
    : undefined,

  // ── Security scoping ──
  // Regions: restricts IAM resource ARNs + Lambda auto-detection to these regions only.
  // Default: [stack region]. Set via env var or hardcode for customer deployments.
  allowedRegions: process.env.ALLOWED_REGIONS
    ? process.env.ALLOWED_REGIONS.split(',').filter(Boolean)
    : undefined,  // defaults to [stack region]

  // Cluster names: restricts ssm:SendCommand to instances tagged with these exact
  // eks:cluster-name values. Prevents targeting instances in other clusters.
  // SECURITY: production deploys SHOULD set this. To deploy without it (any
  // EKS cluster in the account), set ALLOW_ANY_CLUSTER_NAME=true to make the
  // wildcard scope explicit.
  allowedClusterNames: process.env.ALLOWED_CLUSTER_NAMES
    ? process.env.ALLOWED_CLUSTER_NAMES.split(',').filter(Boolean)
    : undefined,
  allowAnyClusterName: process.env.ALLOW_ANY_CLUSTER_NAME === 'true',

  // SSM documents: restricts which documents can be executed via SendCommand.
  // Default: ['AWS-RunShellScript'] only.
  allowedSsmDocuments: process.env.ALLOWED_SSM_DOCUMENTS
    ? process.env.ALLOWED_SSM_DOCUMENTS.split(',').filter(Boolean)
    : undefined,  // defaults to ['AWS-RunShellScript']

  // Presigned URL expiry: max 900s (15 min). Lower = less exposure if URL is intercepted.
  presignedUrlExpirationSeconds: process.env.PRESIGNED_URL_EXPIRATION
    ? parseInt(process.env.PRESIGNED_URL_EXPIRATION, 10)
    : 300,

  // Tighter expiry for raw network captures from tcpdump_*.
  pcapPresignedUrlExpirationSeconds: process.env.PCAP_PRESIGNED_URL_EXPIRATION
    ? parseInt(process.env.PCAP_PRESIGNED_URL_EXPIRATION, 10)
    : 60,

  // Optional VPC + interface endpoints. When set, Lambda runs inside the VPC
  // and S3/KMS/SSM/EC2/EKS/Logs traffic stays on private AWS network.
  vpcId: process.env.MCP_VPC_ID || undefined,
  vpcSubnetIds: process.env.MCP_VPC_SUBNET_IDS
    ? process.env.MCP_VPC_SUBNET_IDS.split(',').filter(Boolean)
    : undefined,

  // Per-tool authorization map.
  // Format: TOOL_AUTHORIZATION="collect:client-a,client-b;tcpdump_capture:client-emergency"
  toolAuthorization: process.env.TOOL_AUTHORIZATION
    ? Object.fromEntries(
        process.env.TOOL_AUTHORIZATION.split(';')
          .filter(Boolean)
          .map(entry => {
            const [tool, clients] = entry.split(':', 2);
            return [tool.trim(), (clients ?? '').split(',').map(s => s.trim()).filter(Boolean)];
          }),
      )
    : undefined,

  perCallerRateLimitPerMinute: process.env.PER_CALLER_RATE_LIMIT_PER_MINUTE
    ? parseInt(process.env.PER_CALLER_RATE_LIMIT_PER_MINUTE, 10)
    : 60,

  maxPcapBytes: process.env.MAX_PCAP_BYTES
    ? parseInt(process.env.MAX_PCAP_BYTES, 10)
    : 209715200,

  // Restricted tools: tcpdump_capture and tcpdump_analyze are removed from the
  // tool routing table by default. They do not appear in available_tools and
  // cannot be invoked. Only enable if the customer has explicitly approved
  // network capture capabilities on their nodes.
  enableRestrictedTools: process.env.ENABLED_RESTRICTED_TOOLS
    ? process.env.ENABLED_RESTRICTED_TOOLS.split(',').filter(Boolean)
    : undefined,  // defaults to [] (tcpdump not available)
});
