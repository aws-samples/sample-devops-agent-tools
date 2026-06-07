# Contributing Guidelines

Thank you for your interest in contributing to our project. Whether it's a bug report, new feature, correction, or additional
documentation, we greatly value feedback and contributions from our community.

Please read through this document before submitting any issues or pull requests to ensure we have all the necessary
information to effectively respond to your bug report or contribution.

## Contributing a New Skill

Before contributing a new skill, make sure you check the following:

1. Check existing skills and pull requests, to make sure that the skill you want to build doesn't already exist, or cannot be extended in an existing skill
2. Check that if DevOps Agent already has the capability for which you'd like to build a skill (it might be capable of doing what you planned without a skill). Test it yourself
3. If you have concluded that a new skill is required, fork the repository and clone the fork

### Write the Skill

Before you begin, review the [DevOps Agent skills documentation](https://docs.aws.amazon.com/devopsagent/latest/userguide/about-aws-devops-agent-devops-agent-skills.html) and the [Agent Skills specification](https://agentskills.io/home), to understand how to properly write a skill.

1. Create a directory for your skill, inside the `skills/` directory
2. Decide which DevOps Agent subagents are relevant to your skill (e.g., Chat tasks, Incident RCA). It'll be important later when you test your skill and instruct your users how to use it
3. Start writing your skill according to the documentation referenced above. If you're working with an AI tool like Kiro or Claude, please note that this repo has Kiro steering docs and CLAUDE.md, with guildelines on how to write skills according to those docs. It's still a good idea to tell those tools to build the skill based on the above docs (although this is mentioned in the steering docs).  
Each skill must have a specific required structure, check the Kiro steering docs or CLAUDE.md to find the structure.
Start with the SKILL.md, as well as `references/` and and `assets/` if needed.
4. Make sure the skill's formatter includes a `metadata` block with `version` and `author` fields
5. Document your skill in a `README.md` file inside your skill's directory
6. Create a `CHANGELOG.md` file for your skill, inside your skill's directory
7. Include a non-production disclaimer in your skill's README. Add a note stating that the skill is sample code, not intended for production use without additional review and testing, and that users should validate in a non-production environment first

### Test Your Skill

1. Test your skill with the DevOps Agent:
   1. Zip and upload the skill to the DevOps Agent space (make sure you select the relevant agents)
   2. Make sure you give DevOps Agent space the necessary permissions for your skill (if they don't already exist in the auto-created role), if the skill needs to interact with AWS APIs
   3. If your skill is meant for a specific AWS service, create the necessary resources, and simulate a relevant scenarios for your skill. For example, if your skill is used to provide DevOps Agent with troubleshooting guidelines for Lambda function issues, you'll have to create a setup with a Lambda function, and simulate actual issues (such as Lambda function ends with an error, Lambda invocation throttling, etc.)
   4. Start testing relevant DevOps Agent functionalities and scenarios. Taking the above Lambda issues example, it could be something like "Investigate the Lambda errors in function xyz", or "Investigate CloudWatch alarm xyz" (if you have CloudWatch alarm for Lambda errors, for example)
   5. If your skill is relevant for multiple DevOps Agent functionalities (such as chat and investigation), make sure you test all those functionalities. For example, the `support-cases` skill in this repo is relevant for chat (e.g., "Generate a support cases report for the last quarter, including breakdowns by service and severity") and for investigations (e.g., "Investigate the CloudWatch alarm xyz")
   6. During the test, review DevOps Agent's reasoning. Make sure you see that it invoked the skill, and that the steps it did make sense. Don't explicitly tell DevOps Agent to use the skill. Remember that an operator doesn't necessarily know which tools DevOps Agent has. An operator will start an investigation with a prompt that describes an issue, and they won't tell DevOps Agent explicitly to use your skill
   7. If you find that DevOps Agent doesn't invoke your skill, or is inconsistent in invoking it, make sure the `description` field in your `SKILL.md` formatter is properly written (see [Optimizing description in the Agent Skills specification](https://agentskills.io/skill-creation/optimizing-descriptions) for more guidelines)
   8. Repeat your tests multiple times to ensure consistency

2. Test your skill locally with [Agent Skill Eval](https://github.com/aws-samples/sample-agent-skill-eval). This tool is meant to evalutate skills according to the [Agent Skills specification](https://agentskills.io/home), to measure safety, quality, reliability, and cost efficiency. It has 3 types of tests - audit (security and structure), functional (evalutate scenarios with/without skill against assertions) and trigger (test skill activation). Go through the following:
   1. Clone the [Agent Skill Eval repo](https://github.com/aws-samples/sample-agent-skill-eval), and follow [Option A for installation](https://github.com/aws-samples/sample-agent-skill-eval#option-a-cli-tool-for-developers). When reaching the `pip install -e .` step, use `pip install -e ".[config]"` instead - it'll also install `pyyaml`, which is necessary
   2. Install Claude CLI. This is required for the functional and trigger tests
   3. Copy the `.skilleval.yaml` file from one of the existing skills, to your skill's directory. It includes a rule to ignore `README.md` file for the audit tests
   4. If your skill interacts with AWS APIs, create a `.claude` directory in the project's root, and inside it, create a `settings.local.json` file, with the following content:
   ```json
   {
     "env": {
       "AWS_PROFILE": "<your-profile>"
     }
   }
   ```
   
   Replace `<your-profile>` with the AWS profile corresponding to the AWS account where you have the resources for the tests. Claude will use this file to find your AWS profile.

   5. Create `evals/` directory inside your skill directory, and inside it create `evals.json` file (for the functional tests) and `eval_queries.json` (for triggers tests). You can see examples in existing skills. If you use AI tools like Kiro or Claude, you can ask them to generate these files for you, according to the Agent Skill Eval tool. Review the files and make sure that the functional tests really test relevant prompts with relevant assertions. Note for `eval_queries.json` - you're only required to have `"should_trigger": false` tests. Activation tests (`"should_trigger": true`) are implied by successful functional tests
   6. Run the tests - inside your skill's directory, run `skill-eval --debug-log debug.log report . --timeout 1200 --runs-trigger 1`. This command will run audit, functional and trigger tests. It can take time, so adjust the `--timeout` flag as necessary. It doesn't print anything to the terminal until the end of each test, so you should expect to see it run almost silently for a long time. A `debug.log` file will be created in your skill's directory and will be updated with logs throuhgout the run. When done, `benchmark.json`, and `report.json` and `trigger_report.json` files will be created in your skill's `evals/` directory. If you want to start small, you can run each test separately (`skill-eval audit`, `skill-eval functional` and `skill-eval trigger`)
   7. Review the `report.json` file, and check the `passed` field, which must be `true` (so, `"passed": true`). It's important that not only some tests will pass, but the overall report should show as passed (meaning, you need to check the `passed` field in the root of the JSON object, not in each section). We only accept skills with `"passed": true`. If your skill shows `"passed": false`, check the `sections` field to find which tests have not passed (there should be `"passed": false` in either `audit`, `functional` or `trigger` tests in this case). If the failed tests are `functional` or `trigger`, review the `debug.log` file to find which tests failed, and either adjust the instructions in your `SKILL.md`, or adjust the tests, as necessary. If the failed tests are `audit`, run `skill-eval audit . --verbose` and fix the failed fidings

### Create Pull Request

See [Contributing via Pull Requests](CONTRIBUTING.md#contributing-via-pull-requests)


## Reporting Bugs/Feature Requests

We welcome you to use the GitHub issue tracker to report bugs or suggest features.

When filing an issue, please check existing open, or recently closed, issues to make sure somebody else hasn't already
reported the issue. Please try to include as much information as you can. Details like these are incredibly useful:

* A reproducible test case or series of steps
* The version of our code being used
* Any modifications you've made relevant to the bug
* Anything unusual about your environment or deployment


## Contributing via Pull Requests
Contributions via pull requests are much appreciated. Before sending us a pull request, please ensure that:

1. You are working against the latest source on the *main* branch.
2. You check existing open, and recently merged, pull requests to make sure someone else hasn't addressed the problem already.
3. You open an issue to discuss any significant work - we would hate for your time to be wasted.

To send us a pull request, please:

1. Fork the repository.
2. Modify the source; please focus on the specific change you are contributing. If you also reformat all the code, it will be hard for us to focus on your change.
3. Ensure local tests pass.
4. Commit to your fork using clear commit messages.
5. Send us a pull request, answering any default questions in the pull request interface.
6. Pay attention to any automated CI failures reported in the pull request, and stay involved in the conversation.

GitHub provides additional document on [forking a repository](https://help.github.com/articles/fork-a-repo/) and
[creating a pull request](https://help.github.com/articles/creating-a-pull-request/).


## Finding contributions to work on
Looking at the existing issues is a great way to find something to contribute on. As our projects, by default, use the default GitHub issue labels (enhancement/bug/duplicate/help wanted/invalid/question/wontfix), looking at any 'help wanted' issues is a great place to start.


## Code of Conduct
This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct).
For more information see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq) or contact
opensource-codeofconduct@amazon.com with any additional questions or comments.


## Security issue notifications
If you discover a potential security issue in this project we ask that you notify AWS/Amazon Security via our [vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/). Please do **not** create a public github issue.


## Licensing

See the [LICENSE](LICENSE) file for our project's licensing. We will ask you to confirm the licensing of your contribution.
