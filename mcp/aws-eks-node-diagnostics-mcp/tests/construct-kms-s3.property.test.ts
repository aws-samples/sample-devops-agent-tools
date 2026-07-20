/**
 * Property-based tests for KMS key policy, S3 bucket policy, and presigned URL config.
 * Validates Properties 1, 8, 9, 10 from the design document.
 */
import * as cdk from 'aws-cdk-lib';
import { Template, Match } from 'aws-cdk-lib/assertions';
import * as fc from 'fast-check';
import { SsmAutomationGatewayV2Construct } from '../src/ssm-automation-gateway-construct-v2';

const regionArb = fc.tuple(
  fc.constantFrom('us', 'eu', 'ap', 'sa', 'ca'),
  fc.constantFrom('east-1', 'east-2', 'west-1', 'west-2', 'central-1'),
).map(([p, s]) => `${p}-${s}`);

const regionsArb: fc.Arbitrary<string[]> = fc.uniqueArray(regionArb, { minLength: 1, maxLength: 3 });

const roleArnArb = fc.nat({ max: 999999999999 }).map(
  n => `arn:aws:iam::${String(n).padStart(12, '0')}:role/eks-node-role`
);
const roleArnsArb: fc.Arbitrary<string[]> = fc.uniqueArray(roleArnArb, { minLength: 1, maxLength: 3 });

function synthesize(props: {
  allowedRegions?: string[];
  eksNodeRoleArns?: string[];
  presignedUrlExpirationSeconds?: number;
  enableEncryption?: boolean;
}) {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'TestStack', { env: { region: 'us-east-1', account: '123456789012' } });
  // allowAnyClusterName: tests rely on the wildcard cluster scope, so opt in
  // to keep them deterministic. Production deploys should set allowedClusterNames.
  new SsmAutomationGatewayV2Construct(stack, 'Gateway', { ...props, allowAnyClusterName: true });
  return Template.fromStack(stack);
}

function getKmsKeyPolicies(template: Template): any[] {
  const keys = template.findResources('AWS::KMS::Key');
  const policies: any[] = [];
  for (const [, resource] of Object.entries(keys)) {
    const stmts = (resource as any).Properties?.KeyPolicy?.Statement;
    if (stmts) policies.push(...stmts);
  }
  return policies;
}

function getBucketPolicies(template: Template): any[] {
  const policies = template.findResources('AWS::S3::BucketPolicy');
  const stmts: any[] = [];
  for (const [, resource] of Object.entries(policies)) {
    const doc = (resource as any).Properties?.PolicyDocument;
    if (doc?.Statement) stmts.push(...doc.Statement);
  }
  return stmts;
}


/**
 * Property 1: CDK allowed regions environment variable round-trip
 */
describe('Property 1: CDK allowed regions environment variable round-trip', () => {
  it('ALLOWED_REGIONS env var matches input regions', () => {
    fc.assert(
      fc.property(regionsArb, (regions) => {
        const template = synthesize({ allowedRegions: regions });
        const functions = template.findResources('AWS::Lambda::Function');
        // Find the SSM automation function
        const ssmFn = Object.entries(functions).find(([id]) => id.includes('SSMAutomation'));
        expect(ssmFn).toBeDefined();
        const envVars = (ssmFn![1] as any).Properties?.Environment?.Variables;
        expect(envVars?.ALLOWED_REGIONS).toBeDefined();
        const parsed = envVars.ALLOWED_REGIONS.split(',').sort();
        expect(parsed).toEqual([...regions].sort());
      }),
      { numRuns: 30 },
    );
  });
});

/**
 * Property 8: KMS policy scoping with eksNodeRoleArns
 */
describe('Property 8: KMS policy scoping with eksNodeRoleArns', () => {
  it('no AnyPrincipal when eksNodeRoleArns provided, specific roles present', () => {
    fc.assert(
      fc.property(roleArnsArb, (arns) => {
        const template = synthesize({ eksNodeRoleArns: arns });
        const stmts = getKmsKeyPolicies(template);

        // Should NOT have AllowAccountPrincipalsEncrypt with AnyPrincipal (*)
        const anyPrincipalStmts = stmts.filter((s: any) =>
          s.Principal === '*' &&
          s.Sid === 'AllowAccountPrincipalsEncrypt'
        );
        expect(anyPrincipalStmts.length).toBe(0);

        // Should have AllowEKSNodeRolesEncrypt with the specific ARNs
        const eksStmts = stmts.filter((s: any) => s.Sid === 'AllowEKSNodeRolesEncrypt');
        expect(eksStmts.length).toBe(1);
        const principals = eksStmts[0].Principal?.AWS;
        const principalList = Array.isArray(principals) ? principals : [principals];
        expect(principalList.sort()).toEqual([...arns].sort());

        // Actions should be encrypt-only
        const actions = eksStmts[0].Action;
        const actionList = Array.isArray(actions) ? actions : [actions];
        expect(actionList).toContain('kms:GenerateDataKey');
        expect(actionList).toContain('kms:Encrypt');
        expect(actionList).not.toContain('kms:Decrypt');
      }),
      { numRuns: 30 },
    );
  });

  it('backward-compatible reduced AnyPrincipal when no eksNodeRoleArns', () => {
    const template = synthesize({});
    const stmts = getKmsKeyPolicies(template);
    // CDK synthesizes AnyPrincipal as {"AWS": "*"}
    const anyPrincipalStmts = stmts.filter((s: any) => {
      const p = s.Principal;
      const sid = s.Sid || '';
      return sid === 'AllowAccountPrincipalsEncrypt' ||
        (p === '*') ||
        (p?.AWS === '*') ||
        (JSON.stringify(p) === '"*"');
    }).filter((s: any) => {
      // Must have account condition
      return s.Condition?.StringEquals?.['aws:PrincipalAccount'];
    });
    expect(anyPrincipalStmts.length).toBe(1);
    const actions = anyPrincipalStmts[0].Action;
    const actionList = Array.isArray(actions) ? actions : [actions];
    expect(actionList).toContain('kms:GenerateDataKey');
    expect(actionList).toContain('kms:Encrypt');
    expect(actionList).not.toContain('kms:Decrypt');
    expect(actionList).not.toContain('kms:DescribeKey');
    expect(actionList).not.toContain('kms:ReEncrypt*');
  });
});

/**
 * Property 9: S3 bucket policy scoping with eksNodeRoleArns
 */
describe('Property 9: S3 bucket policy scoping with eksNodeRoleArns', () => {
  it('uses specific role ARNs when eksNodeRoleArns provided', () => {
    fc.assert(
      fc.property(roleArnsArb, (arns) => {
        const template = synthesize({ eksNodeRoleArns: arns });
        const stmts = getBucketPolicies(template);
        const uploadStmts = stmts.filter((s: any) => s.Sid === 'AllowEC2InstancesUpload');
        expect(uploadStmts.length).toBeGreaterThanOrEqual(1);

        for (const stmt of uploadStmts) {
          const principal = stmt.Principal?.AWS;
          const principalList = Array.isArray(principal) ? principal : [principal];
          expect(principalList.sort()).toEqual([...arns].sort());
          // Verify PutObject action preserved
          const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
          expect(actions).toContain('s3:PutObject');
          // Verify account condition preserved
          expect(stmt.Condition?.StringEquals?.['aws:PrincipalAccount']).toBeDefined();
        }
      }),
      { numRuns: 30 },
    );
  });
});

/**
 * Property 10: Presigned URL expiration configuration round-trip
 */
describe('Property 10: Presigned URL expiration configuration round-trip', () => {
  it('PRESIGNED_URL_EXPIRATION_SECONDS env var matches input', () => {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 86400 }), (seconds) => {
        const template = synthesize({ presignedUrlExpirationSeconds: seconds });
        const functions = template.findResources('AWS::Lambda::Function');
        const ssmFn = Object.entries(functions).find(([id]) => id.includes('SSMAutomation'));
        expect(ssmFn).toBeDefined();
        const envVars = (ssmFn![1] as any).Properties?.Environment?.Variables;
        expect(envVars?.PRESIGNED_URL_EXPIRATION_SECONDS).toBe(String(seconds));
      }),
      { numRuns: 50 },
    );
  });

  it('defaults to 300 when not provided', () => {
    const template = synthesize({});
    const functions = template.findResources('AWS::Lambda::Function');
    const ssmFn = Object.entries(functions).find(([id]) => id.includes('SSMAutomation'));
    expect(ssmFn).toBeDefined();
    const envVars = (ssmFn![1] as any).Properties?.Environment?.Variables;
    expect(envVars?.PRESIGNED_URL_EXPIRATION_SECONDS).toBe('300');
  });
});
