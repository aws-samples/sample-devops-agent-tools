import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { EcsLogGatewayConstructV2, EcsLogGatewayV2Props } from './ecs-log-gateway-construct-v2';

export interface EcsLogGatewayStackV2Props extends cdk.StackProps {
  /**
   * Properties for the ECS Log Gateway construct
   */
  readonly gatewayProps?: EcsLogGatewayV2Props;
}

/**
 * Production-grade ECS Instance Log Collection MCP Server Stack
 *
 * Features:
 * - Async task pattern with SSM Run Command
 * - Byte-range streaming for large files
 * - Manifest validation and completeness verification
 * - AI-ready incident summary generation
 * - KMS encryption at rest
 * - Comprehensive ECS/Docker log collection
 */
export class EcsLogGatewayStackV2 extends cdk.Stack {
  public readonly gateway: EcsLogGatewayConstructV2;

  constructor(scope: Construct, id: string, props?: EcsLogGatewayStackV2Props) {
    super(scope, id, props);

    this.gateway = new EcsLogGatewayConstructV2(this, 'EcsLogGateway', props?.gatewayProps);
  }
}
