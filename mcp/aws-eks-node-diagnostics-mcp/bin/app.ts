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

  // Optional VPC + interface endpoints. When set, Lambda runs inside the VPC
  // and S3/KMS/SSM/EC2/EKS/Logs traffic stays on private AWS network.
  vpcId: process.env.MCP_VPC_ID || undefined,
  vpcSubnetIds: process.env.MCP_VPC_SUBNET_IDS
    ? process.env.MCP_VPC_SUBNET_IDS.split(',').filter(Boolean)
    : undefined,

  // Per-tool authorization map.
  // Format: TOOL_AUTHORIZATION="collect:client-a,client-b;batch_collect:client-emergency"
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

  // Accept self-managed nodes (only the user-settable kubernetes.io/cluster/*
  // tag). Off by default; when enabled, such nodes are cross-checked against the
  // EKS API. See validate_eks_instance in the Lambda for details.
  allowSelfManagedNodes: process.env.ALLOW_SELF_MANAGED_NODES
    ? ['1', 'true', 'yes'].includes(process.env.ALLOW_SELF_MANAGED_NODES.toLowerCase())
    : undefined,

  // Human-in-the-loop approval for the mutating collection tools (collect,
  // batch_collect). On by default (security review M1/M2): collection runs via
  // a wrapper SSM Automation document whose first step is the native
  // aws:approve action — the execution pauses in SSM until a designated human
  // approves it in the Systems Manager console, then collection proceeds
  // automatically. Set REQUIRE_COLLECTION_APPROVAL=false only for a fully
  // supervised/test deployment.
  requireCollectionApproval: process.env.REQUIRE_COLLECTION_APPROVAL
    ? !['0', 'false', 'no'].includes(process.env.REQUIRE_COLLECTION_APPROVAL.toLowerCase())
    : undefined,

  // IAM principals allowed to approve collections (user/role ARNs, comma
  // separated). REQUIRED when approval is enabled — synth fails without it.
  // Approvers also need ssm:SendAutomationSignal to click Approve/Deny.
  approvalApproverArns: process.env.APPROVAL_APPROVER_ARNS
    ? process.env.APPROVAL_APPROVER_ARNS.split(',').filter(Boolean)
    : undefined,

  // Emails to subscribe to the approval SNS topic (each gets the SSM console
  // approval link when a collection is requested).
  approvalNotificationEmails: process.env.APPROVAL_NOTIFICATION_EMAILS
    ? process.env.APPROVAL_NOTIFICATION_EMAILS.split(',').filter(Boolean)
    : undefined,

  // How long the aws:approve step waits for a human decision (seconds).
  approvalTtlSeconds: process.env.APPROVAL_TTL_SECONDS
    ? parseInt(process.env.APPROVAL_TTL_SECONDS, 10)
    : undefined,

  // Restricted tools opt-in (network packet capture). Absent from the MCP tool
  // surface unless listed. tcpdump_capture is ADDITIONALLY approval-gated: every
  // capture pauses at a native SSM aws:approve step until a designated human
  // approves it in the Systems Manager console (M3).
  // Format: ENABLED_RESTRICTED_TOOLS="tcpdump_capture,tcpdump_analyze"
  enableRestrictedTools: process.env.ENABLED_RESTRICTED_TOOLS
    ? process.env.ENABLED_RESTRICTED_TOOLS.split(',').map(s => s.trim()).filter(Boolean)
    : undefined,

  // Presigned URL expiry for pcap downloads (tighter than log artifacts —
  // captures may contain credentials in transit). Max 300s.
  pcapPresignedUrlExpirationSeconds: process.env.PCAP_PRESIGNED_URL_EXPIRATION
    ? parseInt(process.env.PCAP_PRESIGNED_URL_EXPIRATION, 10)
    : undefined,

  // Size above which a completed pcap is flagged as oversized (advisory).
  maxPcapBytes: process.env.MAX_PCAP_BYTES
    ? parseInt(process.env.MAX_PCAP_BYTES, 10)
    : undefined,
});
