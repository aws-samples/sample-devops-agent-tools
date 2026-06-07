# Skills Catalog

Browse available skills for AWS DevOps Agent. Use the group-by buttons to view skills by agent type, AWS service, or any other dimension.

<div id="skills-catalog-root" data-source="../javascripts/skills-data.json"></div>

## How Skills Work

When you upload a skill to your Agent Space, the agent automatically activates it based on the context of your request. For example:

- Asking *"investigate the RDS connectivity alarm"* with the **Support Cases** skill uploaded will cause the agent to search historical support cases for similar RDS issues.
- Asking *"run an EKS operational review"* with the **EKS Operation Review** skill will trigger a comprehensive cluster assessment.

Skills can be composed — upload multiple skills and the agent will activate whichever ones are relevant to your request.
