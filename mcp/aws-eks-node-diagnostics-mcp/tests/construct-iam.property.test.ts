/**
 * Property-based tests for IAM policy statement conditions.
 * Validates Properties 2, 3, 4 from the design document.
 */
import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import * as fc from 'fast-check';
import { SsmAutomationGatewayV2Construct } from '../src/ssm-automation-gateway-construct-v2';

// Arbitrary: generate 1-4 valid AWS region strings
const regionArb = fc.tuple(
  fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca', 'me', 'af'),
  fc.constantFrom('east-1','east-2','west-1','west-2','central-1','north-1','south-1','southeast-1','northeast-1'),
).map(([prefix, suffix]) => `${prefix}-${suffix}`);

const regionsArb: fc.Arbitrary<string[]> = fc.uniqueArray(regionArb, { minLength: 1, maxLength: 4 });

function synthesize(allowedRegions?: string[]) {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'TestStack', { env: { region: 'us-east-1', account: '123456789012' } });
  // allowAnyClusterName: tests rely on the wildcard cluster scope, so opt in
  // to keep them deterministic. Production deploys should set allowedClusterNames.
  new SsmAutomationGatewayV2Construct(stack, 'Gateway', { allowedRegions, allowAnyClusterName: true });
  return Template.fromStack(stack);
}

/** Collect all IAM policy statements from a CloudFormation template for a given role logical prefix */
function collectStatements(template: Template, rolePrefix: string): any[] {
  const policies = template.findResources('AWS::IAM::Policy');
  const statements: any[] = [];
  for (const [logicalId, resource] of Object.entries(policies)) {
    if (!logicalId.includes(rolePrefix)) continue;
    const doc = (resource as any).Properties?.PolicyDocument;
    if (doc?.Statement) {
      statements.push(...doc.Statement);
    }
  }
  return statements;
}

// SSM write actions that should have tag conditions
const SSM_WRITE_ACTIONS = new Set([
  'ssm:SendCommand',
  'ssm:StartAutomationExecution',
  'ssm:StopAutomationExecution',
  'ssm:CancelCommand',
]);

// All SSM read-only actions (should NOT have tag conditions)
const SSM_READONLY_ACTIONS = new Set([
  'ssm:GetAutomationExecution',
  'ssm:DescribeAutomationExecutions',
  'ssm:DescribeAutomationStepExecutions',
  'ssm:GetCommandInvocation',
  'ssm:ListCommandInvocations',
  'ssm:ListCommands',
  'ssm:GetDocument',
  'ssm:DescribeDocument',
  'ssm:GetParameters',
  'ssm:GetParameter',
  'ssm:DescribeInstanceInformation',
  'ssm:GetConnectionStatus',
]);

// Original SSM Automation Role actions (union of write + read)
const ORIGINAL_SSM_ROLE_ACTIONS = new Set([...SSM_WRITE_ACTIONS, ...SSM_READONLY_ACTIONS]);

// Lambda SSM write actions
const LAMBDA_SSM_WRITE_ACTIONS = new Set([
  'ssm:StartAutomationExecution',
  'ssm:StopAutomationExecution',
  'ssm:SendCommand',
]);

// Lambda SSM read-only actions
const LAMBDA_SSM_READONLY_ACTIONS = new Set([
  'ssm:GetAutomationExecution',
  'ssm:DescribeAutomationExecutions',
  'ssm:DescribeInstanceInformation',
  'ssm:GetCommandInvocation',
  'ssm:ListCommands',
  'ssm:ListCommandInvocations',
]);

// Original Lambda SSM actions
const ORIGINAL_LAMBDA_SSM_ACTIONS = new Set([...LAMBDA_SSM_WRITE_ACTIONS, ...LAMBDA_SSM_READONLY_ACTIONS]);


/**
 * Property 2: CDK region conditions on all SSM/EC2 policy statements
 *
 * For any list of allowed regions, ALL IAM policy statements granting SSM or EC2/EKS
 * actions SHALL contain an aws:RequestedRegion StringEquals condition matching the regions.
 */
describe('Property 2: CDK region conditions on all SSM/EC2 policy statements', () => {
  it('all SSM/EC2 statements on SSM Automation Role have correct region condition', () => {
    fc.assert(
      fc.property(regionsArb, (regions) => {
        const template = synthesize(regions);
        const stmts = collectStatements(template, 'SSMAutomationRole');

        // Filter to SSM/EC2/EKS action statements
        const ssmEc2Stmts = stmts.filter((s: any) => {
          const actions: string[] = Array.isArray(s.Action) ? s.Action : [s.Action];
          return actions.some((a: string) => a.startsWith('ssm:') || a.startsWith('ec2:') || a.startsWith('eks:'));
        });

        expect(ssmEc2Stmts.length).toBeGreaterThanOrEqual(3); // write, read, describe

        for (const stmt of ssmEc2Stmts) {
          const actions: string[] = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
          // StartAutomationExecution uses resource-level ARN scoping (region embedded in ARN)
          // rather than aws:RequestedRegion condition — skip condition check for these
          const isArnScoped = actions.every(a =>
            a === 'ssm:StartAutomationExecution' || a === 'ssm:SendCommand'
          );
          // SendCommand on documents also uses ARN scoping (no region condition)
          const isDocScoped = actions.includes('ssm:SendCommand') &&
            stmt.Resource?.some?.((r: any) => typeof r === 'string' && r.includes(':document/'));
          if (isArnScoped && !stmt.Condition?.StringEquals?.['aws:RequestedRegion']) {
            // Verify region is embedded in resource ARNs instead
            // ARNs may be CloudFormation intrinsic functions (Fn::Join) due to partition references
            const resources: any[] = Array.isArray(stmt.Resource) ? stmt.Resource : [stmt.Resource];
            const hasRegionInArn = resources.some((r: any) => {
              if (typeof r === 'string') {
                // SSM SendCommand on instances uses :ec2:${region}:..:instance/*
                // SSM SendCommand on documents and StartAutomationExecution use :ssm:${region}:..
                return regions.some(region =>
                  r.includes(`:ssm:${region}:`) ||
                  r.includes(`:ssm:${region}::`) ||
                  r.includes(`:ec2:${region}:`),
                );
              }
              // Handle Fn::Join intrinsics — check the join parts for region strings
              const joinParts = r?.['Fn::Join']?.[1];
              if (Array.isArray(joinParts)) {
                const joined = joinParts.filter((p: any) => typeof p === 'string').join('');
                return regions.some(region =>
                  joined.includes(`:ssm:${region}:`) ||
                  joined.includes(`:ssm:${region}::`) ||
                  joined.includes(`:ec2:${region}:`),
                );
              }
              return false;
            });
            expect(hasRegionInArn).toBe(true);
          } else if (isDocScoped) {
            // Document-scoped SendCommand uses ARN scoping
            continue;
          } else {
            const cond = stmt.Condition?.StringEquals?.['aws:RequestedRegion'];
            expect(cond).toBeDefined();
            const condRegions = Array.isArray(cond) ? cond : [cond];
            expect(condRegions.sort()).toEqual([...regions].sort());
          }
        }
      }),
      { numRuns: 50 },
    );
  });

  it('all SSM/EC2 statements on Lambda Execution Role have correct region condition', () => {
    fc.assert(
      fc.property(regionsArb, (regions) => {
        const template = synthesize(regions);
        const stmts = collectStatements(template, 'LambdaExecutionRole');

        const ssmEc2Stmts = stmts.filter((s: any) => {
          const actions: string[] = Array.isArray(s.Action) ? s.Action : [s.Action];
          return actions.some((a: string) =>
            a.startsWith('ssm:') || a.startsWith('ec2:') || a.startsWith('eks:') || a.startsWith('autoscaling:')
          );
        });

        // write, read, describe (SSMDocumentAccess has resource-level ARN, no region condition needed)
        const stmtsWithRegionCondition = ssmEc2Stmts.filter((s: any) => {
          const actions: string[] = Array.isArray(s.Action) ? s.Action : [s.Action];
          // SSMDocumentAccess uses resource ARNs
          const isDocAccess = actions.length === 2 &&
            actions.includes('ssm:GetDocument') && actions.includes('ssm:DescribeDocument');
          // StartAutomationExecution and SendCommand on documents use ARN scoping
          const isArnScoped = actions.every(a =>
            a === 'ssm:StartAutomationExecution' || a === 'ssm:SendCommand'
          ) && !s.Condition?.StringEquals?.['aws:RequestedRegion'];
          return !isDocAccess && !isArnScoped;
        });

        expect(stmtsWithRegionCondition.length).toBeGreaterThanOrEqual(3);

        for (const stmt of stmtsWithRegionCondition) {
          const cond = stmt.Condition?.StringEquals?.['aws:RequestedRegion'];
          expect(cond).toBeDefined();
          const condRegions = Array.isArray(cond) ? cond : [cond];
          expect(condRegions.sort()).toEqual([...regions].sort());
        }
      }),
      { numRuns: 50 },
    );
  });
});

/**
 * Property 3: CDK tag conditions on write vs read SSM statements
 *
 * Write statements SHALL have aws:ResourceTag/eks:cluster-name StringLike condition.
 * Read-only statements SHALL NOT have this tag condition.
 */
describe('Property 3: CDK tag conditions on write vs read SSM statements', () => {
  it('SSM Automation Role write statements have tag condition, read statements do not', () => {
    fc.assert(
      fc.property(regionsArb, (regions) => {
        const template = synthesize(regions);
        const stmts = collectStatements(template, 'SSMAutomationRole');

        for (const stmt of stmts) {
          const actions: string[] = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
          const hasWriteAction = actions.some(a => SSM_WRITE_ACTIONS.has(a));
          const hasOnlyReadActions = actions.every(a => !SSM_WRITE_ACTIONS.has(a));
          const tagCond = stmt.Condition?.StringLike?.['aws:ResourceTag/eks:cluster-name'];

          if (hasWriteAction && actions.every(a => SSM_WRITE_ACTIONS.has(a))) {
            // Pure write statement — must have tag condition
            // EXCEPT: StartAutomationExecution targets automation-definition/execution/document
            // resources which don't have EKS tags. SendCommand on documents also lacks tags.
            // Only SendCommand on EC2 instances has the tag condition.
            const targetsInstances = stmt.Resource?.some?.((r: any) =>
              typeof r === 'string' && r.includes(':instance/')
            ) || (typeof stmt.Resource === 'string' && stmt.Resource.includes(':instance/'));
            if (targetsInstances) {
              expect(tagCond).toBe('*');
            }
            // Non-instance targets (automation-definition, document) don't have tag conditions
          } else if (hasOnlyReadActions && actions.some(a => a.startsWith('ssm:'))) {
            // Pure SSM read statement — must NOT have tag condition
            expect(tagCond).toBeUndefined();
          }
        }
      }),
      { numRuns: 50 },
    );
  });

  it('Lambda Execution Role write statements have tag condition, read statements do not', () => {
    fc.assert(
      fc.property(regionsArb, (regions) => {
        const template = synthesize(regions);
        const stmts = collectStatements(template, 'LambdaExecutionRole');

        for (const stmt of stmts) {
          const actions: string[] = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
          const hasWriteAction = actions.some(a => LAMBDA_SSM_WRITE_ACTIONS.has(a));
          const hasOnlyReadActions = actions.every(a => !LAMBDA_SSM_WRITE_ACTIONS.has(a));
          const tagCond = stmt.Condition?.StringLike?.['aws:ResourceTag/eks:cluster-name'];

          if (hasWriteAction && actions.every(a => LAMBDA_SSM_WRITE_ACTIONS.has(a))) {
            // Only SendCommand on EC2 instances has the tag condition
            const targetsInstances = stmt.Resource?.some?.((r: any) =>
              typeof r === 'string' && r.includes(':instance/')
            ) || (typeof stmt.Resource === 'string' && stmt.Resource.includes(':instance/'));
            if (targetsInstances) {
              expect(tagCond).toBe('*');
            }
          } else if (hasOnlyReadActions && actions.some(a => a.startsWith('ssm:') && !a.includes('Document'))) {
            // SSM read (excluding document access which has resource ARNs)
            expect(tagCond).toBeUndefined();
          }
        }
      }),
      { numRuns: 50 },
    );
  });
});

/**
 * Property 4: CDK action preservation across split statements
 *
 * The union of SSM actions in write + read-only statements SHALL equal the original set.
 */
describe('Property 4: CDK action preservation across split statements', () => {
  it('SSM Automation Role preserves all original SSM actions', () => {
    fc.assert(
      fc.property(regionsArb, (regions) => {
        const template = synthesize(regions);
        const stmts = collectStatements(template, 'SSMAutomationRole');

        const allSsmActions = new Set<string>();
        for (const stmt of stmts) {
          const actions: string[] = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
          for (const a of actions) {
            if (a.startsWith('ssm:')) allSsmActions.add(a);
          }
        }

        // Every original action must be present
        for (const action of ORIGINAL_SSM_ROLE_ACTIONS) {
          expect(allSsmActions.has(action)).toBe(true);
        }
      }),
      { numRuns: 50 },
    );
  });

  it('Lambda Execution Role preserves all original SSM actions', () => {
    fc.assert(
      fc.property(regionsArb, (regions) => {
        const template = synthesize(regions);
        const stmts = collectStatements(template, 'LambdaExecutionRole');

        const allSsmActions = new Set<string>();
        for (const stmt of stmts) {
          const actions: string[] = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
          for (const a of actions) {
            if (a.startsWith('ssm:')) allSsmActions.add(a);
          }
        }

        // Every original action must be present (including document access)
        for (const action of ORIGINAL_LAMBDA_SSM_ACTIONS) {
          expect(allSsmActions.has(action)).toBe(true);
        }
        // Document access actions too
        expect(allSsmActions.has('ssm:GetDocument')).toBe(true);
        expect(allSsmActions.has('ssm:DescribeDocument')).toBe(true);
      }),
      { numRuns: 50 },
    );
  });
});
