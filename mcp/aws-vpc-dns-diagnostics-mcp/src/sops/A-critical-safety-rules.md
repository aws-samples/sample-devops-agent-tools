# A — Critical Safety Rules (apply to ALL recommendations)

These rules constrain every recommendation this server's output feeds into. They
are not optional heuristics.

## 1. Never recommend applying a DNS change without running Mode B first

If the proposed change touches a VPC endpoint private-DNS flag, a Route 53
Resolver rule, a private hosted zone, a DNS Firewall rule group, or a Route 53
Profile association, predict the blast radius before acting. Call
`dns_simulate_change` and report which currently-resolving names would break.

## 2. Agreement between resolvers is not the goal — the correct answer is

For an on-premises or corporate zone, the custom resolver *should* win and the
VPC resolver *should* return NXDOMAIN. That is correct behavior, not a fault.
Never flag divergence as a problem without first classifying the name. See
`A-name-category-classification`.

## 3. Check the VPC-attribute precondition first

If `enableDnsSupport=false`, the VPC resolver is intentionally dark. Lead with
that finding. Do not diagnose it as a routing or security-group problem.

## 4. Never rely on the public internet path for SSM

Mode A requires SSM connectivity through VPC interface endpoints for `ssm`,
`ssmmessages`, and `ec2messages`. If SSM is unreachable, report that as the
blocker. Do not propose opening egress or attaching a public path as a workaround
to make the diagnostic run.

An EC2 Instance Connect Endpoint does **not** satisfy this requirement. Run
Command depends on the SSM Agent polling outbound to `ssmmessages` and
`ec2messages`; EICE is an inbound interactive tunnel and carries no SSM
control-plane traffic. If an instance has EICE but is `Not connected` in SSM, the
remediation is to add the three interface endpoints, not to reach for EICE.

## 5. Respect address family

In IPv6-only subnets `169.254.169.253` does not exist; only `fd00:ec2::253`
answers. Do not report the absent IPv4 resolver as a fault in that context.

## 6. Route 53 Profile changes propagate asynchronously

Service-side propagation runs roughly 300–350 seconds, with a worst case up to
about 900 seconds when a premature query populates a negative cache. State
*when* a change takes effect, not only what the end state will be. See
`B-profile-propagation-timing`.

## 7. Mode A observes one instance

Results reflect the resolver path of the single instance probed, including its
`resolv.conf` and any local stub resolver. Do not generalize to the whole VPC
without probing an instance in each relevant subnet.

## 8. Never present a Mode B prediction as an observed fact

Mode B is symbolic. Say "this change is predicted to break N names" and, where
it matters, confirm with Mode A before the operator acts.
